"""
카메라 영상 중계 (UDP). camera_streamer -> control(hub) -> Vision + instructor_console.

- 카메라마다 UDP 포트 하나에서 받는다 (protocol.VIDEO_PORTS: cam1 6001, cam2 6002)
- 데이터그램 하나 = 헤더 16바이트 + JPEG 한 장 (protocol.VIDEO_HEADER). 헤더·JPEG 시작이 틀리면 버린다.
- 받은 데이터그램은 바꾸지 않고, 지금 접속 중인 instructor_console 의 IP 로 그대로 보낸다
  (protocol.CONSOLE_VIDEO_PORTS: cam1 5011, cam2 5012). Vision에는 별도로 5021/5022로 보낸다.
- 저장하지 않는다 (디스크·DB 모두). 디코딩도 하지 않는다.
- 판단은 하지 않는다. 카메라별 수신 시작·끊김(3초)만 로그로 남긴다.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from common.protocol import CONSOLE_VIDEO_PORTS, VISION_VIDEO_PORTS, VIDEO_HEADER, VIDEO_PORTS

log = logging.getLogger("hub.video")

STALE_S = 3.0                # 이 시간 동안 프레임이 없으면 끊김으로 로그
JPEG_SOI = b"\xff\xd8"


@dataclass
class CamStats:
    cam: int
    live: bool = False
    last_rx: float = 0.0
    peer: str = ""
    received: int = 0        # 받은 프레임 (누적)
    forwarded: int = 0       # Console 로 넘긴 프레임 (누적)
    dropped: int = 0         # 형식이 틀려 버린 데이터그램 (누적)


class _CamProtocol(asyncio.DatagramProtocol):
    def __init__(self, relay: "VideoRelay", cam: int):
        self.relay, self.cam = relay, cam
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        self.relay.on_datagram(self, data, addr)

    def error_received(self, exc):
        # Console 쪽 포트가 닫혀 있으면 ICMP 로 여기 온다. 다음 프레임은 계속 보낸다.
        log.debug("cam%d UDP 오류: %s", self.cam, exc)


class VideoRelay:
    def __init__(self, host: str, console_ip, ports: dict | None = None,
                 console_ports: dict | None = None, vision_ip=None):
        """console_ip() = 지금 접속 중인 instructor_console 의 IP (없으면 None)."""
        self.host = host
        self.vision_ip = vision_ip or (lambda: None)
        self.console_ip = console_ip
        self.ports = dict(ports or VIDEO_PORTS)
        self.console_ports = dict(console_ports or CONSOLE_VIDEO_PORTS)
        self.stats = {cam: CamStats(cam) for cam in self.ports}
        self._protocols: list[_CamProtocol] = []
        self._watch: asyncio.Task | None = None
        self._no_console_logged = False

    async def start(self):
        """포트를 못 열어도 control 은 계속 돈다 (영상만 안 됨)."""
        loop = asyncio.get_running_loop()
        for cam, port in self.ports.items():
            try:
                _, proto = await loop.create_datagram_endpoint(
                    lambda cam=cam: _CamProtocol(self, cam), local_addr=(self.host, port))
            except OSError as e:
                log.error("cam%d 영상 포트 udp %s:%d 를 열 수 없음 (%s) - 이 카메라 영상은 중계하지 않음",
                          cam, self.host, port, e)
                continue
            self._protocols.append(proto)
            log.info("영상 중계: cam%d udp :%d -> instructor_console :%d", cam, port, self.console_ports[cam])
        self._watch = asyncio.create_task(self._watch_loop())

    def close(self):
        if self._watch:
            self._watch.cancel()
        for p in self._protocols:
            if p.transport:
                p.transport.close()
        self._protocols.clear()

    # ---- 수신 -> 중계 ---------------------------------------------------
    def on_datagram(self, proto: _CamProtocol, data: bytes, addr):
        st = self.stats[proto.cam]
        hs = VIDEO_HEADER.size
        if len(data) <= hs + 2 or data[0] != proto.cam or data[hs:hs + 2] != JPEG_SOI:
            st.dropped += 1
            log.debug("cam%d 형식이 틀린 데이터그램 %d바이트 (%s) - 버림", proto.cam, len(data), addr[0])
            return
        now = time.time()
        st.received += 1
        st.last_rx, st.peer = now, f"{addr[0]}:{addr[1]}"
        if not st.live:
            st.live = True
            log.info("cam%d 영상 수신 시작 (%s)", proto.cam, st.peer)

        vision_ip = self.vision_ip()
        if vision_ip:
            proto.transport.sendto(data, (vision_ip, VISION_VIDEO_PORTS[proto.cam]))
        ip = self.console_ip()
        if ip is None:
            if not self._no_console_logged:
                self._no_console_logged = True
                log.info("instructor_console 미접속 - Console 중계 생략 (Vision 중계는 별도)")
            return
        self._no_console_logged = False
        proto.transport.sendto(data, (ip, self.console_ports[proto.cam]))
        st.forwarded += 1

    async def _watch_loop(self):
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            for st in self.stats.values():
                if st.live and now - st.last_rx > STALE_S:
                    st.live = False
                    log.warning("cam%d 영상 끊김 (%.0f초 동안 프레임 없음, 지금까지 받음 %d / 넘김 %d / 버림 %d)",
                                st.cam, now - st.last_rx, st.received, st.forwarded, st.dropped)
