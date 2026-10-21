"""IF-05 Control ↔ Instructor Console (TCP 바이너리)

QTcpSocket을 메인 스레드 이벤트 루프에서 쓴다. 스레드를 따로 두지 않아도
readyRead 시그널로 받기 때문에 화면이 멈추지 않는다.
- 접속 직후 node_info, 2초마다 heartbeat
- 끊기면 2초마다 다시 접속 (4.1)
- 6초 동안 Control에서 아무것도 안 오면 끊긴 것으로 보고 다시 접속
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket, QTcpSocket

from common import protocol as P

CONSOLE_VERSION = "console-0.1"


class ControlLink(QObject):
    message = pyqtSignal(str, dict)        # (type 이름, 본문)
    connected_changed = pyqtSignal(bool)
    rtt_changed = pyqtSignal(float)        # ms

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port
        self.sock = QTcpSocket(self)
        self.reader = P.FrameReader()
        self.seq = P.SeqCounter()
        self.connected = False
        self.last_rx = 0.0

        self.sock.connected.connect(self._on_connected)
        self.sock.disconnected.connect(self._on_disconnected)
        self.sock.errorOccurred.connect(self._on_error)
        self.sock.readyRead.connect(self._on_ready_read)

        self.reconnect_timer = QTimer(self, singleShot=True, interval=2000)
        self.reconnect_timer.timeout.connect(self.start)
        self.hb_timer = QTimer(self, interval=2000)
        self.hb_timer.timeout.connect(self._heartbeat)

    # ── 연결 ────────────────────────────────────────
    def start(self):
        if self.sock.state() == QAbstractSocket.SocketState.UnconnectedState:
            self.reader = P.FrameReader()
            self.sock.connectToHost(self.host, self.port)

    def _on_connected(self):
        self.connected = True
        self.last_rx = time.time()
        self.send(P.NODE_INFO, {"seat": 0, "version": CONSOLE_VERSION})
        self.hb_timer.start()
        self.connected_changed.emit(True)       # 이어서 Control이 snapshot을 보낸다

    def _on_disconnected(self):
        self.hb_timer.stop()
        if self.connected:
            self.connected = False
            self.connected_changed.emit(False)
        self.reconnect_timer.start()

    def _on_error(self, _err):
        # 접속 실패도 여기로 온다. 연결된 적이 없으면 disconnected가 안 오므로 직접 재시도 예약
        if self.sock.state() == QAbstractSocket.SocketState.UnconnectedState:
            self._on_disconnected()

    # ── 송수신 ──────────────────────────────────────
    def send(self, msg_type: int, body: dict | None = None) -> bool:
        if not self.connected:
            return False
        self.sock.write(P.encode(msg_type, P.NODE_CONSOLE, self.seq.next(), body))
        return True

    def _heartbeat(self):
        if time.time() - self.last_rx > 6.0:     # Control 무응답 → 끊고 다시 접속
            self.sock.abort()
            self._on_disconnected()
            return
        self.send(P.HEARTBEAT)                   # Console heartbeat는 본문 없음

    def _on_ready_read(self):
        self.last_rx = time.time()
        for header, name, body in self.reader.feed(bytes(self.sock.readAll().data())):
            if name is None:                     # 모르는 msg_type은 건너뛴다
                continue
            if name == "heartbeat_ack":
                self.rtt_changed.emit((time.time() - body["echo_ts"]) * 1000.0)
                continue
            self.message.emit(name, body)
