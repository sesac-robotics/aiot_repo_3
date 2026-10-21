"""학생 평소 화면 (화면 구성도 v3 학생 ①): 내 웹캠 미리보기 + 집중·자세 상태(본인만) + [자리 등록] [알림 끄기]

위: [N번 좌석] 버튼(누르면 강의실 지도) · 이름 · 연결 상태
가운데: 웹캠 미리보기 (저장 안 함)
아래: 집중 / 자세 카드, 버튼
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from student_gui.notifier import Notifier

OK, WARN, IDLE = "#e3fcef", "#fffae6", "#f4f5f7"


def card(title: str) -> tuple[QFrame, QLabel]:
    box = QFrame()
    box.setStyleSheet(f"QFrame{{background:{IDLE}; border-radius:10px;}}")
    t = QLabel(title)
    t.setStyleSheet("color:#6b778c; font-size:13px;")
    v = QLabel("-")
    v.setStyleSheet("font-size:18px; font-weight:bold; color:#172b4d;")
    lay = QVBoxLayout(box)
    lay.addWidget(t)
    lay.addWidget(v)
    return box, v


class StudentWindow(QMainWindow):
    register_clicked = pyqtSignal()
    seat_clicked = pyqtSignal()
    closed = pyqtSignal()                      # 쓰러짐 창은 따로 뜬 창이라, 이 창을 닫으면 앱을 끝낸다

    def __init__(self, seat: int):
        super().__init__()
        self.setWindowTitle(f"AI 조교 - {seat}번 좌석")
        self.resize(760, 640)
        self.face_labels, self.posture_labels = {}, {}

        self.seat_btn = QPushButton(f"{seat}번 좌석")
        self.seat_btn.setToolTip("강의실 지도에서 내 위치 보기")
        self.seat_btn.setStyleSheet("font-size:15px; font-weight:bold; padding:6px 14px;")
        self.name_label = QLabel("미등록")
        self.name_label.setStyleSheet("font-size:15px;")
        self.conn_label = QLabel()
        self.set_connected(False)
        top = QHBoxLayout()
        top.addWidget(self.seat_btn)
        top.addWidget(self.name_label)
        top.addStretch(1)
        top.addWidget(self.conn_label)

        self.video = QLabel("카메라 준비 중...")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(480, 360)
        self.video.setStyleSheet("background:#091e42; color:#b3bac5; border-radius:8px;")
        self.privacy = QLabel("영상은 이 노트북 화면에만 보이고 저장하지 않습니다. 강사님께는 반 평균만 전달됩니다.")
        self.privacy.setStyleSheet("color:#6b778c; font-size:12px;")

        self.focus_box, self.focus_val = card("집중")
        self.posture_box, self.posture_val = card("자세")
        cards = QHBoxLayout()
        cards.addWidget(self.focus_box)
        cards.addWidget(self.posture_box)

        self.reg_btn = QPushButton("자리 등록")
        self.mute_btn = QPushButton("알림 끄기")
        self.mute_btn.setCheckable(True)
        for b in (self.reg_btn, self.mute_btn):
            b.setMinimumHeight(40)
            b.setStyleSheet("font-size:15px; padding:0 18px;")
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.reg_btn)
        btns.addWidget(self.mute_btn)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addLayout(top)
        lay.addWidget(self.video, 1)
        lay.addWidget(self.privacy)
        lay.addLayout(cards)
        lay.addLayout(btns)
        self.setCentralWidget(root)
        self.notifier = Notifier(root)

        self.reg_btn.clicked.connect(self.register_clicked)
        self.seat_btn.clicked.connect(self.seat_clicked)
        self.mute_btn.toggled.connect(self._toggle_mute)

    def closeEvent(self, e):
        super().closeEvent(e)
        self.closed.emit()

    # ---- 갱신 -----------------------------------------------------------
    def set_name(self, name: str | None):
        self.name_label.setText(name or "미등록 - [자리 등록]을 눌러 주세요")

    def set_connected(self, ok: bool):
        self.conn_label.setText("● 연결됨" if ok else "● 연결 안 됨 (재접속 중)")
        self.conn_label.setStyleSheet(f"color:{'#006644' if ok else '#bf2600'}; font-size:13px;")

    def show_frame(self, img: QImage):
        pix = QPixmap.fromImage(img).scaled(self.video.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        self.video.setPixmap(pix)

    def show_camera_error(self, text: str):
        self.video.setText(text + "\n(노트북 카메라 연결을 확인해 주세요)")

    def show_state(self, s: dict):
        if s.get("calibrating"):
            self._set(self.focus_box, self.focus_val, "등록 중", IDLE)
            self._set(self.posture_box, self.posture_val, "등록 중", IDLE)
        elif not s.get("present"):
            self._set(self.focus_box, self.focus_val, "자리 비움", IDLE)
            self._set(self.posture_box, self.posture_val, "-", IDLE)
        else:
            face = [self.face_labels.get(k, k) for k in s.get("face", ())]
            pos = [self.posture_labels.get(k, k) for k in s.get("posture", ())]
            self._set(self.focus_box, self.focus_val, ", ".join(face) or "좋아요", WARN if face else OK)
            self._set(self.posture_box, self.posture_val, ", ".join(pos) or "바른 자세", WARN if pos else OK)
        self.notifier.update_state(s)

    @staticmethod
    def _set(box, label, text, color):
        if label.text() != text:
            label.setText(text)
            box.setStyleSheet(f"QFrame{{background:{color}; border-radius:10px;}}")

    def _toggle_mute(self, on: bool):
        self.notifier.muted = on
        self.mute_btn.setText("알림 켜기" if on else "알림 끄기")
        if on:
            self.notifier.hide()
