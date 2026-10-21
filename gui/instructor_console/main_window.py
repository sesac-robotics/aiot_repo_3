"""강사 화면 메인 창 — 세로 탭(홈 · 전체 화면 · 이벤트 · 시설 관리), 설정은 왼쪽 아래"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
                             QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from common import protocol as P
from gui.alert_sound import AlertSound      # 쓰러짐 알림음 (학생 화면과 공용)

from .facility import FacilityPage, LivePage, SettingsDialog
from .home import HomePage, LogQueries
from .net import ControlLink
from .store import Store
from .video import UdpFrameSource


class MainWindow(QMainWindow):
    def __init__(self, host: str, port: int, cam1_port: int, cam2_port: int, clip_root: str):
        super().__init__()
        self.setWindowTitle("Jarvis 강의실 AI 조교 — 강사 화면")
        self.resize(1440, 900)

        self.store = Store(self)
        self.link = ControlLink(host, port, self)
        self.link.message.connect(self.store.apply)
        self.link.connected_changed.connect(self.on_connection)
        self.link.rtt_changed.connect(lambda ms: self.rtt.setText(f"{ms:.0f}ms"))
        send = self.send
        self.logs = LogQueries(send, self.store, self)
        self.alarm = AlertSound(self)

        # ── 세로 탭 ──
        side = QFrame(objectName="sideBar")
        side.setFixedWidth(104)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(8, 14, 8, 14)
        sv.setSpacing(6)
        logo = QLabel("Jarvis", alignment=Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("color:#fff; font-size:17px; font-weight:700; padding-bottom:10px;")
        sv.addWidget(logo)
        self.group = QButtonGroup(self)
        self.tabs = {}
        for key, text in [("home", "홈"), ("live", "전체 화면"), ("event", "이벤트"), ("facility", "시설 관리")]:
            b = QToolButton(text=text, checkable=(key != "event"))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setMinimumHeight(48)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, b.sizePolicy().verticalPolicy())
            if key != "event":
                self.group.addButton(b)
            sv.addWidget(b)
            self.tabs[key] = b
        sv.addStretch(1)
        self.conn = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.rtt = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.rtt.setStyleSheet("color:#90a4ae; font-size:11px;")
        self.clock = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.clock.setStyleSheet("color:#fff; font-size:15px; font-weight:600;")
        settings = QToolButton(text="⚙ 설정")
        settings.setMinimumHeight(40)
        settings.clicked.connect(self.open_settings)
        for w in (self.clock, self.conn, self.rtt, settings):
            sv.addWidget(w)

        # ── 페이지 ──
        self.pages = QStackedWidget()
        # 카메라마다 UDP 포트는 한 번만 연다. cam2는 이벤트 창과 시설 관리(각도 등록)가 같이 본다
        self.cam1 = UdpFrameSource(cam1_port, cam_id=1, parent=self)
        self.cam2 = UdpFrameSource(cam2_port, cam_id=2, parent=self)
        self.home = HomePage(self.store, send, self.logs, self.cam2, clip_root)
        self.live = LivePage(self.cam1)
        self.facility = FacilityPage(self.store, send, self.cam2)
        for p in (self.home, self.live, self.facility):
            self.pages.addWidget(p)

        self.offline = QLabel("Control 서버에 연결되지 않았습니다 — 2초마다 다시 연결합니다", objectName="offlineBanner")
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.offline)
        body.addWidget(self.pages, 1)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(side)
        h.addLayout(body, 1)
        self.setCentralWidget(root)

        self.tabs["home"].clicked.connect(lambda: self.pages.setCurrentWidget(self.home))
        self.tabs["live"].clicked.connect(lambda: self.pages.setCurrentWidget(self.live))
        self.tabs["facility"].clicked.connect(lambda: self.pages.setCurrentWidget(self.facility))
        self.tabs["event"].clicked.connect(self.open_latest_unconfirmed)
        self.tabs["home"].setChecked(True)

        # ── 상태 변화 연결 ──
        self.store.snapshot_done.connect(self.on_snapshot)
        self.store.seats_changed.connect(self.home.seat_map.refresh)
        self.store.event_changed.connect(self.on_event)
        self.store.queue_changed.connect(self.home.tick)
        self.store.devices_changed.connect(self.home.tick)

        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.on_connection(False)
        self.tick()
        self.link.start()

    # ── 연결 ──
    def send(self, msg_type: int, body: dict | None = None) -> bool:
        """모든 버튼이 이걸로 보낸다. 끊겨서 못 보냈으면 강사가 알 수 있게 아래에 알린다."""
        if self.link.send(msg_type, body):
            return True
        self.statusBar().showMessage("Control 서버에 연결되지 않아 보내지 못했습니다 — 연결되면 다시 눌러 주세요", 5000)
        return False

    def on_connection(self, ok: bool):
        self.offline.setVisible(not ok)
        self.conn.setText("● 연결됨" if ok else "● 끊김")
        self.conn.setStyleSheet(f"color:{'#81c995' if ok else '#f28b82'}; font-size:12px;")
        if not ok:
            self.rtt.setText("")
            self.logs.reset()

    def on_snapshot(self):
        self.home.log.search()
        self.home.stats.refresh_today()
        self.update_badge()
        self.home.tick()

    # ── 이벤트 ──
    def on_event(self, event_id: int, is_new: bool):
        if self.store.in_snapshot:
            return
        ev = self.store.events[event_id]
        if is_new and ev["kind"] == P.KIND_FALL and self.store.alert_sound_on():
            self.alarm.play()
            QApplication.alert(self, 3000)
        self.update_badge()
        self.home.log.on_event_changed()
        if is_new:
            self.home.stats.refresh_today()
        self.home.tick()

    def update_badge(self):
        n = len(self.store.unconfirmed())
        self.tabs["event"].setText(f"이벤트\n● {n}" if n else "이벤트")
        self.tabs["event"].setStyleSheet("color:#ffb4ab; font-weight:700;" if n else "")

    def open_latest_unconfirmed(self):
        """[이벤트] 탭 = 가장 최근의 확인 안 한 이벤트를 좌석도 자리에 연다"""
        self.tabs["home"].setChecked(True)
        self.pages.setCurrentWidget(self.home)
        evs = self.store.unconfirmed()
        if evs:
            self.home.open_event(evs[0]["event_id"])
        else:
            self.home.show_map()
            self.statusBar().showMessage("확인 안 한 이벤트가 없습니다", 3000)

    def open_settings(self):
        SettingsDialog(self.store, self.send, self).exec()

    def tick(self):
        self.clock.setText(time.strftime("%H:%M:%S"))
        self.home.tick()
