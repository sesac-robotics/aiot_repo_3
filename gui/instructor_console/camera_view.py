"""강사 화면 [수동보기] 좌석 카메라 보기 (좌석도 자리에 열림, 10/22)

AI 조교에게 "1번 좌석 보여줘" 하면 Control(event_manager)이 팬틸트를 그 좌석으로 돌리고 view_state 를 보낸다.
이 창은 view_state {seat, until} 를 받아 팬틸트 카메라(cam2) 실시간 영상과 남은 시간을 보여 준다.
- 이벤트를 추적 중이면 Control 이 거절하므로 이 창은 열리지 않는다 (AI 조교가 이유를 답함)
- until 이 지나거나 새 이벤트가 오면 Control 이 view_state {seat: null} → 창이 닫히고 좌석도로
- [원위치] = view_request {seat: null}. [좌석도로] 는 창만 닫는다 (카메라는 남은 시간 동안 그대로)
"""
from __future__ import annotations

import time
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .video import LiveVideo, UdpFrameSource


class CameraViewPanel(QWidget):
    closed = pyqtSignal()

    def __init__(self, cam2: UdpFrameSource, send: Callable[[str, dict], bool]):
        super().__init__()
        self.send = send
        self.state: dict | None = None
        from .home import card                        # 같은 카드 모양 (순환 import 피하려고 여기서)
        f, lay = card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(f)

        top = QHBoxLayout()
        self.title = QLabel(objectName="cardTitle")
        self.title.setStyleSheet("font-size:17px; font-weight:700; color:#4a148c;")
        self.left = QLabel(objectName="muted")
        home = QPushButton("원위치", objectName="purple")
        home.setToolTip("팬틸트 카메라를 원위치로 돌리고 좌석도로 돌아갑니다")
        home.clicked.connect(lambda: self.send("view_request", {"seat": None}))
        back = QPushButton("좌석도로")
        back.setToolTip("창만 닫습니다. 카메라는 남은 시간 동안 그 좌석을 봅니다")
        back.clicked.connect(self.closed)
        top.addWidget(self.title, 1)
        top.addWidget(self.left)
        top.addWidget(home)
        top.addWidget(back)
        lay.addLayout(top)

        self.live = LiveVideo(cam2, title="팬틸트 카메라 실시간")
        lay.addWidget(self.live, 1)
        note = QLabel("AI 조교 요청으로 돌린 수동 보기입니다 · 녹화하지 않음 · 새 도움 요청이 오면 바로 그 좌석으로 넘어갑니다",
                      objectName="muted", wordWrap=True)
        lay.addWidget(note)

    def show_state(self, m: dict):
        self.state = m
        seat = m.get("seat")
        self.title.setText(f"{seat}번 좌석 · 팬틸트 카메라 (수동 보기)" if seat else "수동 보기 끝")
        self.tick()

    def tick(self):
        m = self.state or {}
        until = m.get("until")
        if m.get("seat") and until:
            self.left.setText(f"{max(0, int(until - time.time()))}초 뒤 원위치")
        else:
            self.left.setText("")
