"""
모든 노드가 같이 쓰는 control 접속 클라이언트 (SA 설계서 3·5·10절, 상세 설계 0장)

- 백그라운드 스레드가 접속·재접속(2초 간격)·하트비트(2초)·수신을 맡는다.
- 노드 코드는 카메라 루프 안에서 client.send(...) 만 부르면 된다 (asyncio 몰라도 됨).
- 끊긴 동안 사건 이벤트와 버튼 입력(confirm·resolve·fall_confirm)은 버퍼에 모았다가
  재접속하면 보낸다. heartbeat·track_offset 같은 상태 메시지는 버린다(곧 새 값이 오므로).

사용 예:
    client = NodeClient("vision_service", "192.168.0.23", on_message=print)
    client.start()
    client.send("question", seat=4, value={"state": "start"}, conf=0.9)
    client.set_fps(14.8)
    client.set_status(cam1_fps=15.0, cam2_fps=14.2)   # 하트비트에 같이 실려 감
    ...
    client.stop()
"""

import collections
import logging
import os
import platform
import socket
import threading

from common.protocol import (BUFFERED_TYPES, DEFAULT_PORT, HEARTBEAT_INTERVAL_S,
                             MessageError, encode, make_message, parse_message)

log = logging.getLogger("client")

RECONNECT_INTERVAL_S = 2.0
PENDING_MAX = 100          # 끊긴 동안 모아 둘 사건 메시지 수


class NodeClient:
    def __init__(self, node: str, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 on_message=None):
        self.node = node
        self.host, self.port = host, port
        self.on_message = on_message          # control 이 보낸 메시지를 받을 함수 (msg) -> None

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()          # 소켓 쓰기·seq 보호
        self._stop = threading.Event()
        self._seq = 0
        self._fps = 0.0
        self._status: dict = {}            # 하트비트에 같이 실을 값 (cam1_fps 등)
        self._info: dict | None = None     # 접속할 때마다 보낼 node_info (카메라·좌석)
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

    def set_status(self, **fields):
        """하트비트에 같이 실을 값. 예: set_status(cam1_fps=15.0, cam2_fps=14.2)"""
        self._status = {**self._status, **fields}   # 새 dict 로 바꿔 끼움 (하트비트 스레드가 읽는 중이어도 안전)

    def set_info(self, info: dict):
        """노드 부가 정보. 접속(재접속 포함)할 때마다 node_info 로 한 번 보낸다.
        이 PC 정보(pc)는 set_info 를 안 불러도 자동으로 붙는다."""
        self._info = info
        if self.connected:
            self._send_node_info()

    def send(self, type_: str, seat=None, value=None, conf: float = 1.0) -> bool:
        """메시지 하나를 보낸다. 보냈으면 True, 끊겨서 못 보냈으면 False."""
        with self._lock:
            self._seq += 1
            msg = make_message(self.node, self._seq, type_, seat, value, conf)
            if self._sock is None:             # 끊겼거나 재접속 중(밀린 것 보내는 중): 순서대로 뒤에 붙인다
                if type_ in BUFFERED_TYPES:
                    self._pending.append(msg)
                return False
        if self._write(msg):
            return True
        if type_ in BUFFERED_TYPES:
            self._pending.append(msg)          # 사건·버튼 입력은 재접속 후 다시 보냄
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
                log.info("control(%s:%d) 접속 실패: %s - %.0f초 후 재시도",
                         self.host, self.port, e, RECONNECT_INTERVAL_S)
                self._stop.wait(RECONNECT_INTERVAL_S)
                continue

            if not self._handshake(sock):
                try:
                    sock.close()
                except OSError:
                    pass
                self._stop.wait(RECONNECT_INTERVAL_S)
                continue
            log.info("control 접속: %s:%d (node=%s)", self.host, self.port, self.node)
            self._recv_loop(sock)              # 끊길 때까지 여기서 대기
            self._close()
            if not self._stop.is_set():
                log.warning("control 연결 끊김 - %.0f초 후 재접속", RECONNECT_INTERVAL_S)
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
                    log.warning("control 메시지 오류: %s", e)
                    continue
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception:
                        log.exception("on_message 처리 중 오류")

    def _handshake(self, sock: socket.socket) -> bool:
        """하트비트(노드 등록) -> node_info -> 밀린 메시지 를 보낸 뒤에야 self._sock 을 연다.
        그동안 다른 스레드의 send() 는 _pending 뒤에 붙으므로 순서가 바뀌지 않는다
        (밀린 start 보다 새 end 가 먼저 가는 일이 없다)."""
        with self._lock:
            try:
                for type_, value in (("heartbeat", self._heartbeat_value()),
                                     ("node_info", self._node_info_value(sock))):
                    self._seq += 1
                    sock.sendall(encode(make_message(self.node, self._seq, type_, None, value)))
                while self._pending:
                    msg = self._pending[0]
                    sock.sendall(encode(msg))
                    self._pending.popleft()
                    log.info("밀린 사건 전송: %s seq=%d", msg["type"], msg["seq"])
            except OSError as e:
                log.warning("접속 직후 전송 실패: %s", e)
                return False
            self._sock = sock
        return True

    def _pc_info(self, sock: socket.socket | None = None) -> dict:
        """이 클라이언트가 돌아가는 PC를 구별할 정보."""
        ip = None
        if sock is not None:
            try:
                ip = sock.getsockname()[0]     # control 쪽으로 나가는 내 IP
            except OSError:
                pass
        return {"hostname": socket.gethostname(), "ip": ip,
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(), "pid": os.getpid()}

    def _node_info_value(self, sock: socket.socket | None) -> dict:
        return {**(self._info or {}), "pc": self._pc_info(sock)}

    def _send_node_info(self):
        with self._lock:
            sock = self._sock
        self.send("node_info", value=self._node_info_value(sock))

    def _heartbeat_value(self) -> dict:
        try:
            cpu = os.getloadavg()[0] / (os.cpu_count() or 1)
        except (OSError, AttributeError):      # Windows 등
            cpu = None
        return {**self._status, "fps": round(self._fps, 1),
                "cpu": None if cpu is None else round(cpu, 2)}

    def _send_heartbeat(self):
        self.send("heartbeat", value=self._heartbeat_value())

    def _heartbeat_loop(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL_S):
            if self.connected:
                self._send_heartbeat()
