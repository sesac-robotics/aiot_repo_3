"""
모든 노드가 같이 쓰는 hub 접속 클라이언트 (SA 설계서 3·5·10절)

- 백그라운드 스레드가 접속·재접속(2초 간격)·하트비트(2초)·수신을 맡는다.
- 노드 코드는 카메라 루프 안에서 client.send(...) 만 부르면 된다 (asyncio 몰라도 됨).
- 끊긴 동안 사건 이벤트는 버퍼에 모았다가 재접속하면 보낸다.
  presence·heartbeat·track_state 같은 상태 메시지는 버린다(곧 새 값이 오므로).

사용 예:
    client = NodeClient("fixedcam", "192.168.0.23", on_message=print)
    client.start()
    client.send("hand_raise", seat=4, value={"state": "start"}, conf=0.9)
    client.set_fps(14.8)
    ...
    client.stop()
"""

import collections
import logging
import os
import socket
import threading
import time

from common.message import (DEFAULT_PORT, INCIDENT_PRIORITY, MessageError,
                            encode, make_message, parse_message)

log = logging.getLogger("client")

RECONNECT_INTERVAL_S = 2.0
HEARTBEAT_INTERVAL_S = 2.0
PENDING_MAX = 100          # 끊긴 동안 모아 둘 사건 메시지 수


class NodeClient:
    def __init__(self, node: str, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 on_message=None):
        self.node = node
        self.host, self.port = host, port
        self.on_message = on_message          # hub 가 보낸 메시지를 받을 함수 (msg) -> None

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()          # 소켓 쓰기·seq 보호
        self._stop = threading.Event()
        self._seq = 0
        self._fps = 0.0
        self._info: dict | None = None     # 접속할 때마다 보낼 node_info
        self._pending = collections.deque(maxlen=PENDING_MAX)
        self._threads: list[threading.Thread] = []

    # ---- 공개 API -------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._sock is not None

    def start(self):
        for target in (self._connect_loop, self._heartbeat_loop):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        self._close()

    def set_fps(self, fps: float):
        """노드 루프에서 잰 fps 를 알려 주면 하트비트에 실려 간다."""
        self._fps = fps

    def set_info(self, info: dict):
        """카메라·좌석 정보. 접속(재접속 포함)할 때마다 node_info 로 한 번 보낸다."""
        self._info = info
        if self.connected:
            self.send("node_info", value=info)

    def send(self, type_: str, seat=None, value=None, conf: float = 1.0) -> bool:
        """메시지 하나를 보낸다. 보냈으면 True, 끊겨서 못 보냈으면 False."""
        with self._lock:
            self._seq += 1
            msg = make_message(self.node, self._seq, type_, seat, value, conf)
        if self._write(msg):
            return True
        if type_ in INCIDENT_PRIORITY:
            self._pending.append(msg)          # 사건은 재접속 후 다시 보냄
        return False

    # ---- 내부 -----------------------------------------------------------
    def _write(self, msg: dict) -> bool:
        with self._lock:
            sock = self._sock
            if sock is None:
                return False
            try:
                sock.sendall(encode(msg))
                return True
            except OSError as e:
                log.warning("전송 실패: %s", e)
        self._close()
        return False

    def _close(self):
        with self._lock:
            sock, self._sock = self._sock, None
        if sock:
            try:
                sock.close()
            except OSError:
                pass

    def _connect_loop(self):
        while not self._stop.is_set():
            try:
                sock = socket.create_connection((self.host, self.port), timeout=3)
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError as e:
                log.info("hub(%s:%d) 접속 실패: %s - %.0f초 후 재시도",
                         self.host, self.port, e, RECONNECT_INTERVAL_S)
                self._stop.wait(RECONNECT_INTERVAL_S)
                continue

            with self._lock:
                self._sock = sock
            log.info("hub 접속: %s:%d (node=%s)", self.host, self.port, self.node)
            self._send_heartbeat()             # 첫 메시지로 노드 등록
            if self._info is not None:
                self.send("node_info", value=self._info)
            self._flush_pending()
            self._recv_loop(sock)              # 끊길 때까지 여기서 대기
            self._close()
            if not self._stop.is_set():
                log.warning("hub 연결 끊김 - %.0f초 후 재접속", RECONNECT_INTERVAL_S)
                self._stop.wait(RECONNECT_INTERVAL_S)

    def _recv_loop(self, sock: socket.socket):
        buf = b""
        while not self._stop.is_set():
            try:
                data = sock.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = parse_message(line)
                except MessageError as e:
                    log.warning("hub 메시지 오류: %s", e)
                    continue
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        log.exception("on_message 처리 중 오류")

    def _flush_pending(self):
        while self._pending:
            msg = self._pending.popleft()
            if not self._write(msg):
                self._pending.appendleft(msg)
                return
            log.info("밀린 사건 전송: %s seq=%d", msg["type"], msg["seq"])

    def _send_heartbeat(self):
        try:
            cpu = os.getloadavg()[0] / (os.cpu_count() or 1)
        except (OSError, AttributeError):      # Windows 등
            cpu = None
        self.send("heartbeat", value={"fps": round(self._fps, 1),
                                      "cpu": None if cpu is None else round(cpu, 2)})

    def _heartbeat_loop(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL_S):
            if self.connected:
                self._send_heartbeat()
