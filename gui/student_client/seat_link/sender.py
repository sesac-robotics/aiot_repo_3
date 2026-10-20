"""바이너리 패킷 TCP 송신기: 백그라운드 스레드가 접속·재접속을 맡고, send()는 판정 루프를 막지 않는다

- 끊겨 있는 동안 보낸 패킷은 버린다 (상태 요약이라 다음 주기에 새 값이 감)
- 사용 예:
    sender = PacketSender("127.0.0.1", 6000); sender.start()
    sender.send(packet.encode(1, {"drowsy": True}))
    sender.stop()
"""
import logging
import socket
import threading

log = logging.getLogger("seat_link")

RECONNECT_S = 2.0
CONNECT_TIMEOUT_S = 2.0


class PacketSender:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self._sock = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.sent = 0                               # 보낸 패킷 수
        self.dropped = 0                            # 끊겨서 버린 패킷 수

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def start(self):
        self._thread = threading.Thread(target=self._connect_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def send(self, data: bytes) -> bool:
        with self._lock:
            sock = self._sock
            if sock is None:
                self.dropped += 1
                return False
            try:
                sock.sendall(data)
                self.sent += 1
                return True
            except OSError as e:
                log.warning("전송 실패, 다시 접속합니다: %s", e)
                self.dropped += 1
                self._sock = None
        try:
            sock.close()
        except OSError:
            pass
        return False

    def _close(self):
        with self._lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _connect_loop(self):
        while not self._stop.is_set():
            if self._sock is None:
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
                    sock.settimeout(None)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    with self._lock:
                        self._sock = sock
                    log.info("접속: %s:%d", self.host, self.port)
                except OSError:
                    pass
            self._stop.wait(RECONNECT_S)