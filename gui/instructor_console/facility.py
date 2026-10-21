"""강사 화면 ④ 시설 관리 · 설정 · 전체 화면 (화면 구성도 v3)"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                             QWidget)

from common import protocol as P

from . import theme as T
from .home import card
from .store import LAYOUT_NEIGHBORS, SEATS, Store, neighbors_to_mask
from .video import LiveVideo, UdpFrameSource

DEVICE_ORDER = [P.NODE_CONTROL, P.NODE_VISION, P.NODE_CAM1, P.NODE_CAM2, P.NODE_PANTILT, 11, 12, 13, 14]


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

        # ── 2. 좌석 · 장비 등록 ──
        f2, l2 = card("좌석 · 장비 등록")
        l2.addWidget(QLabel("좌석 번호는 1~4로 고정, 옆자리는 2×2 배치로 자동입니다. "
                            "학생 이름·바른 자세는 학생 화면 [자리 등록]에서 학생이 직접 등록합니다.",
                            objectName="muted", wordWrap=True))
        grid = QGridLayout()
        for c, h in enumerate(["좌석", "옆자리", "학생 노트북", "학생", ""]):
            grid.addWidget(QLabel(h, objectName="muted"), 0, c)
        self.seat_rows = {}
        for r, s in enumerate(SEATS, start=1):
            dev = QLineEdit(placeholderText=f"student_client_{s}")
            name = QLabel()
            save = QPushButton("저장")
            save.clicked.connect(lambda _=False, seat=s: self.save_seat(seat))
            grid.addWidget(QLabel(f"{s}번"), r, 0)
            grid.addWidget(QLabel(", ".join(f"{n}번" for n in LAYOUT_NEIGHBORS[s])), r, 1)
            grid.addWidget(dev, r, 2)
            grid.addWidget(name, r, 3)
            grid.addWidget(save, r, 4)
            self.seat_rows[s] = (dev, name)
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
        for text, r, c, dp, dtl in [("▲ 위", 0, 1, 0, 1), ("◀ 왼쪽", 1, 0, -1, 0), ("▶ 오른쪽", 1, 2, 1, 0),
                                    ("▼ 아래", 2, 1, 0, -1)]:
            b = QPushButton(text)
            b.setAutoRepeat(True); b.setAutoRepeatInterval(250)
            b.clicked.connect(lambda _=False, a=dp, t=dtl: self.jog(a, t))
            pad.addWidget(b, r, c)
        save = QPushButton("현재 각도로 저장", objectName="primary")
        save.clicked.connect(lambda: self.send(P.PRESET_SAVE, {"seat": self.pt_seat.currentData()}))
        pad.addWidget(save, 1, 1)
        l3.addLayout(pad)
        l3.addWidget(QLabel("방향 부호(설치 방향)는 presets.json의 control.direction에서 바뀔 수 있습니다. "
                            "범위: Pan 0~180°, Tilt 30~140°", objectName="muted", wordWrap=True))
        self.preset_state = QLabel()
        l3.addWidget(self.preset_state)
        lay.addWidget(f3, 5)

        store.devices_changed.connect(self.refresh_devices)
        store.seats_changed.connect(self.refresh_seats)

    # ── 동작 ──
    def jog(self, dpan: int, dtilt: int):
        step = self.pt_step.currentData()
        self.send(P.PAN_TILT_JOG, {"dpan": dpan * step, "dtilt": dtilt * step})

    def save_seat(self, seat: int):
        dev, _ = self.seat_rows[seat]
        self.send(P.SEAT_CONFIG, {"seat": seat, "neighbors": neighbors_to_mask(LAYOUT_NEIGHBORS[seat]),
                                  "device": dev.text().strip() or dev.placeholderText()})

    # ── 표시 ──
    def refresh_devices(self):
        nodes = [n for n in DEVICE_ORDER if n in self.store.devices] + \
                sorted(n for n in self.store.devices if n not in DEVICE_ORDER)
        self.dev_table.setRowCount(len(nodes))
        for r, n in enumerate(nodes):
            d = self.store.devices[n]
            name = T.node_name(n) + (f" · {d['seat']}번 좌석" if d["seat"] else "")
            state = "연결됨" if d["online"] else "끊김"
            if d["online"] and d["rtt_ms"]:
                state += f" ({d['rtt_ms']}ms)"
            if d["kind"] == P.DEV_STUDENT and d["online"] and not d["cam_ok"]:
                state += " · 카메라 꺼짐"
            cells = [name, T.DEVICE_KIND_NAME.get(d["kind"], str(d["kind"])), state,
                     "" if d["online"] else (d["note"] or "")]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if not d["online"]:                       # 끊긴 장비는 주황 줄
                    it.setBackground(QColor(T.OFFLINE_ORANGE))
                if c == 2:
                    it.setForeground(QColor("#188038" if d["online"] else "#e8710a"))
                self.dev_table.setItem(r, c, it)

    def refresh_seats(self):
        for s, (dev, name) in self.seat_rows.items():
            info = self.store.seats[s]
            if not dev.hasFocus():
                dev.setText(info["device"])
            name.setText(self.store.student_name(s) or "미등록")
        done = [f"{s}번 {'✔' if self.store.seats[s]['has_preset'] else '✖'}" for s in SEATS]
        self.preset_state.setText("각도 등록: " + "  ".join(done))


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
    """[설정] — app_setting 키 4개 (Data Structure v2). 바뀐 값만 setting_set으로 보낸다."""

    def __init__(self, store: Store, send: Callable, parent=None):
        super().__init__(parent)
        self.store, self.send = store, send
        self.setWindowTitle("설정")
        form = QFormLayout(self)
        self.sound = QCheckBox("쓰러짐 알림음 켜기")
        self.rest_ratio = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2)
        self.rest_hold = QSpinBox(minimum=30, maximum=3600, singleStep=30, suffix=" 초")
        self.keep_days = QSpinBox(minimum=1, maximum=30, suffix=" 일")
        form.addRow("알림음", self.sound)
        form.addRow("휴식 권장 비율 (집중 낮은 학생)", self.rest_ratio)
        form.addRow("휴식 권장 유지 시간", self.rest_hold)
        form.addRow("녹화 보관 기간", self.keep_days)
        form.addRow(QLabel("나머지 기준값은 control.yaml에서 바꿉니다.", objectName="muted"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        s = store.setting
        self.sound.setChecked(store.alert_sound_on())
        self.rest_ratio.setValue(float(s("rest_ratio", "0.5") or 0.5))
        self.rest_hold.setValue(int(float(s("rest_hold_s", "300") or 300)))
        self.keep_days.setValue(int(float(s("clip_keep_days", "7") or 7)))

    def save(self):
        new = {"alert_sound": "1" if self.sound.isChecked() else "0",
               "rest_ratio": f"{self.rest_ratio.value():g}",
               "rest_hold_s": str(self.rest_hold.value()),
               "clip_keep_days": str(self.keep_days.value())}
        for k, v in new.items():
            if self.store.setting(k) != v:
                self.send(P.SETTING_SET, {"key": k, "value": v})
        self.accept()
