"""Control ↔ Instructor Console (TCP 바이너리 프레임, common/protocol.py)

QTcpSocket을 메인 스레드 이벤트 루프에서 쓴다. 스레드를 따로 두지 않아도
readyRead 시그널로 받기 때문에 화면이 멈추지 않는다.
- 접속 직후 heartbeat(노드 등록) + node_info, 2초마다 heartbeat
- 끊기면 2초마다 다시 접속
- 주고받는 값은 protocol.make_message() 의 dict 그대로 (type·node 는 문자열)
- Control 은 Console 에 주기적으로 보내는 메시지가 없으므로, 받은 게 없다고 끊지 않는다.
  끊김은 TCP 연결 종료로만 판단한다.
"""
from __future__ import annotations

import os
import platform
import socket

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtNetwork import QAbstractSocket, QTcpSocket

from common import protocol as P

CONSOLE_VERSION = "console-0.2"


class ControlLink(QObject):
    message = pyqtSignal(str, dict)        # (type 이름, value)
    connected_changed = pyqtSignal(bool)

    def __init__(self, host: str, port: int, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port
        self.sock = QTcpSocket(self)
        self.reader = P.FrameReader()
        self.seq = 0
        self.connected = False

        self.sock.connected.connect(self._on_connected)
        self.sock.disconnected.connect(self._on_disconnected)
        self.sock.errorOccurred.connect(self._on_error)
        self.sock.readyRead.connect(self._on_ready_read)

        self.reconnect_timer = QTimer(self, singleShot=True, interval=2000)
        self.reconnect_timer.timeout.connect(self.start)
        self.hb_timer = QTimer(self, interval=int(P.HEARTBEAT_INTERVAL_S * 1000))
        self.hb_timer.timeout.connect(self._heartbeat)

    # ── 연결 ────────────────────────────────────────
    def start(self):
        if self.sock.state() == QAbstractSocket.SocketState.UnconnectedState:
            self.reader = P.FrameReader()
            self.sock.connectToHost(self.host, self.port)

    def _on_connected(self):
        self.connected = True
        self._heartbeat()                        # 첫 메시지로 노드 등록 → Control 이 snapshot 을 보낸다
        self.send("node_info", {"version": CONSOLE_VERSION, "pc": self._pc_info()})
        self.hb_timer.start()
        self.connected_changed.emit(True)

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

    def _pc_info(self) -> dict:
        return {"hostname": socket.gethostname(), "ip": self.sock.localAddress().toString(),
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(), "pid": os.getpid()}

    # ── 송수신 ──────────────────────────────────────
    def send(self, type_: str, value: dict | None = None, seat: int | None = None) -> bool:
        if not self.connected:
            return False
        self.seq += 1
        msg = P.make_message(P.CONSOLE_NODE, self.seq, type_, seat, value)
        self.sock.write(P.encode(msg))
        return True

    def _heartbeat(self):
        self.send("heartbeat")

    def _on_ready_read(self):
        for frame in self.reader.feed(bytes(self.sock.readAll().data())):
            try:
                msg = P.parse_message(frame)
            except P.MessageError as e:          # 깨진 메시지는 건너뛴다 (연결은 유지)
                print(f"[console] Control 메시지 오류: {e}")
                continue
            if msg["type"] == "rx_ack":
                continue
            self.message.emit(msg["type"], msg["value"])
