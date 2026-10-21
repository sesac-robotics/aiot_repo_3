"""강사 화면 ④ 시설 관리 · 설정 · 전체 화면 (화면 구성도 v3)"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from common import protocol as P

from . import theme as T
from .home import card
from .store import LAYOUT_NEIGHBORS, SEATS, Store, settings
from .video import LiveVideo, UdpFrameSource

DEVICE_ORDER = [P.CONTROL_NODE, P.VISION_NODE, T.PANTILT_NODE, P.CONSOLE_NODE, *P.STUDENT_NODES]


class FacilityPage(QWidget):
    def __init__(self, store: Store, send: Callable, cam2: UdpFrameSource):
        super().__init__(objectName="page")
        self.store, self.send = store, send
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(16)

        # ── 1. 장비 상태 ──
        f1, l1 = card("장비 상태")
        l1.addWidget(QLabel("장비는 2초마다 신호를 보내고, 6초 동안 신호가 없으면 Control이 끊김으로 표시합니다.",
                            objectName="muted", wordWrap=True))
        self.dev_table = QTableWidget(0, 4)
        self.dev_table.setHorizontalHeaderLabels(["장비", "종류", "상태", "끊겼을 때 영향"])
        self.dev_table.verticalHeader().setVisible(False)
        self.dev_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.dev_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.dev_table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        l1.addWidget(self.dev_table, 1)
        # 왼쪽: 장비 상태 + 좌석 등록 / 오른쪽: 팬틸트 각도 등록 (영상을 크게 보며 맞춘다)
        left = QVBoxLayout()
        left.setSpacing(16)
        left.addWidget(f1, 1)

        # ── 2. 좌석 · 장비 (보기만) ──
        f2, l2 = card("좌석 · 장비")
        l2.addWidget(QLabel("좌석 번호는 1~4로 고정, 옆자리는 2×2 배치입니다 (Control 의 control.yaml). "
                            "학생 노트북은 좌석 번호로 접속합니다 (student_client_N).",
                            objectName="muted", wordWrap=True))
        grid = QGridLayout()
        for c, h in enumerate(["좌석", "옆자리", "학생 노트북", "학생"]):
            grid.addWidget(QLabel(h, objectName="muted"), 0, c)
        self.seat_rows = {}
        for r, s in enumerate(SEATS, start=1):
            name = QLabel()
            grid.addWidget(QLabel(f"{s}번"), r, 0)
            grid.addWidget(QLabel(", ".join(f"{n}번" for n in LAYOUT_NEIGHBORS[s])), r, 1)
            grid.addWidget(QLabel(P.student_node(s)), r, 2)
            grid.addWidget(name, r, 3)
            self.seat_rows[s] = name
        grid.setColumnStretch(2, 1)
        l2.addLayout(grid)
        left.addWidget(f2)
        lay.addLayout(left, 5)

        # ── 3. 팬틸트 각도 등록 ──
        f3, l3 = card("팬틸트 각도 등록")
        why = QLabel("① 등록한 각도로 크게 빠르게 이동 → ② 영상으로 학생을 찾아 작게 정확하게. "
                     "화각이 좁은 카메라도 빠르고 정확하게 움직이고, 좌석이 늘어도 각도만 추가하면 됩니다.",
                     objectName="muted", wordWrap=True)
        l3.addWidget(why)
        # 각도는 팬틸트 카메라 영상을 보며 맞춘다. 이벤트 창과 같은 cam2 수신기를 같이 쓴다 (포트는 한 번만 열림)
        self.pt_video = LiveVideo(cam2, title="팬틸트 카메라 (cam2)", crosshair=True)
        self.pt_video.setMinimumSize(400, 300)
        l3.addWidget(self.pt_video, 1)
        l3.addWidget(QLabel("학생 얼굴이 십자선 가운데에 오도록 움직인 뒤 [현재 각도로 저장]을 누르세요.",
                            objectName="muted", wordWrap=True))
        top = QHBoxLayout()
        self.pt_seat = QComboBox()
        for s in SEATS:
            self.pt_seat.addItem(f"{s}번 좌석", s)
        self.pt_step = QComboBox()
        for st in (1, 2, 5, 10):
            self.pt_step.addItem(f"{st}°씩", st)
        self.pt_step.setCurrentIndex(2)
        top.addWidget(self.pt_seat); top.addWidget(self.pt_step); top.addStretch(1)
        l3.addLayout(top)
        pad = QGridLayout()
        # 버튼 방향 = 영상이 움직이는 방향 (실물 장착 방향에 맞춰 각도 부호를 뒤집음: 위 = tilt 감소, 왼쪽 = pan 증가)
        for text, r, c, dp, dtl in [("▲ 위", 0, 1, 0, -1), ("◀ 왼쪽", 1, 0, 1, 0), ("▶ 오른쪽", 1, 2, -1, 0),
                                    ("▼ 아래", 2, 1, 0, 1)]:
            b = QPushButton(text)
            b.setAutoRepeat(True); b.setAutoRepeatInterval(250)
            b.clicked.connect(lambda _=False, a=dp, t=dtl: self.jog(a, t))
            pad.addWidget(b, r, c)
        save = QPushButton("현재 각도로 저장", objectName="primary")
        save.clicked.connect(lambda: self.send("preset_save", {"seat": self.pt_seat.currentData()}))
        pad.addWidget(save, 1, 1)
        l3.addLayout(pad)
        l3.addWidget(QLabel("화살표 방향이 반대면 위 버튼 목록의 부호를 바꿉니다 (자동 추적 방향은 presets.json의 "
                            "control.direction). 범위: Pan 0~180°, Tilt 30~140°", objectName="muted", wordWrap=True))
        self.preset_state = QLabel()
        l3.addWidget(self.preset_state)
        lay.addWidget(f3, 5)

        store.devices_changed.connect(self.refresh_devices)
        store.seats_changed.connect(self.refresh_seats)
        self.refresh_seats()

    # ── 동작 ──
    def jog(self, dpan: int, dtilt: int):
        step = self.pt_step.currentData()
        self.send("pan_tilt_jog", {"dpan": dpan * step, "dtilt": dtilt * step})

    # ── 표시 ──
    def refresh_devices(self):
        nodes = [n for n in DEVICE_ORDER if n in self.store.devices] + \
                sorted(n for n in self.store.devices if n not in DEVICE_ORDER)
        self.dev_table.setRowCount(len(nodes))
        for r, n in enumerate(nodes):
            d = self.store.devices[n]
            cells = [T.node_name(n), T.node_kind(n), "연결됨" if d["online"] else "끊김",
                     "" if d["online"] else (d.get("note") or "")]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if not d["online"]:                       # 끊긴 장비는 주황 줄
                    it.setBackground(QColor(T.OFFLINE_ORANGE))
                if c == 2:
                    it.setForeground(QColor("#188038" if d["online"] else "#e8710a"))
                self.dev_table.setItem(r, c, it)

    def refresh_seats(self):
        for s, name in self.seat_rows.items():
            name.setText(self.store.student_name(s) or "미등록")
        done = []
        for s in SEATS:
            p = self.store.seats[s]["preset"]
            done.append(f"{s}번 ✔ ({p[0]}°, {p[1]}°)" if p else f"{s}번 {'✔' if self.store.seats[s]['has_preset'] else '—'}")
        self.preset_state.setText("이번 실행에서 저장한 각도: " + "  ".join(done))


class LivePage(QWidget):
    """[전체 화면] 탭: 고정 카메라 실시간 (화면 구성도 v3 '그림 추가 예정')"""

    def __init__(self, cam1: UdpFrameSource):
        super().__init__(objectName="page")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        f, l = card("전체 화면 — 고정 카메라 실시간")
        l.addWidget(LiveVideo(cam1, title="고정 카메라 (cam1)"), 1)
        lay.addWidget(f)


class SettingsDialog(QDialog):
    """[설정] — 이 PC 에만 저장하는 화면 설정. 판정 기준값은 Control 의 control.yaml 에서 바꾼다."""

    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("설정")
        form = QFormLayout(self)
        self.sound = QCheckBox("쓰러짐 알림음 켜기")
        form.addRow("알림음", self.sound)
        form.addRow(QLabel("나머지 기준값(알림 시간, 만료 등)은 control.yaml에서 바꿉니다.", objectName="muted"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.sound.setChecked(store.alert_sound_on())

    def save(self):
        settings().setValue("alert_sound", self.sound.isChecked())
        self.accept()
