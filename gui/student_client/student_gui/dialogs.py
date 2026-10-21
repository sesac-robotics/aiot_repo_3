"""학생 화면 대화상자: 자리 등록 / 강의실 지도 / 쓰러짐 확인 요청 (화면 구성도 v3 학생 ⓪·③·④)"""
from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
                             QWidget)

STAGE_KO = {"neighbor": "옆자리 확인 요청", "all": "반 전체 확인 요청"}
SEAT_POS = {1: (0, 0), 2: (1, 0), 3: (0, 1), 4: (1, 1)}       # 2×2 배치 (1 2 / 3 4)
ARM_MS = 1000           # 쓰러짐 창이 뜬 뒤 이 시간 동안은 [확인했어요]를 눌러도 안 된다 (잘못 누름 방지)


class RegisterDialog(QDialog):
    """⓪ 자리 등록: 이름 입력 → [등록 시작] → 바른 자세 10초 (→ 영역 값이 없으면 영역 측정, [다음])"""
    start_requested = pyqtSignal(str)          # 이름
    next_requested = pyqtSignal()              # 영역 측정 다음 단계 (Space)

    def __init__(self, seat: int, name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"자리 등록 - {seat}번 좌석")
        self.setMinimumWidth(460)
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("이름")
        self.start_btn = QPushButton("등록 시작")
        self.next_btn = QPushButton("다음 (Space)")
        self.next_btn.hide()
        self.guide = QLabel(f"좌석 번호는 이 노트북에 고정입니다: {seat}번\n"
                            "이름을 입력하고 [등록 시작]을 누르면 바른 자세를 10초 동안 잽니다.\n"
                            "허리를 펴고 화면 정면을 봐 주세요.")
        self.guide.setWordWrap(True)
        self.guide.setStyleSheet("font-size:14px; padding:8px; background:#f4f5f7; border-radius:6px;")
        self.note = QLabel()                   # 측정 중에 닫으려 할 때 안내
        self.note.setStyleSheet("color:#bf2600; font-size:12px;")
        self.note.hide()
        row = QHBoxLayout()
        row.addWidget(QLabel("이름"))
        row.addWidget(self.name_edit, 1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        btns.addWidget(self.next_btn)
        btns.addWidget(self.start_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.guide)
        lay.addWidget(self.note)
        lay.addLayout(btns)
        self.start_btn.clicked.connect(self._start)
        self.next_btn.clicked.connect(self.next_requested)
        self.measuring = False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and self.next_btn.isVisible():
            self.next_requested.emit()
            return
        super().keyPressEvent(e)

    def reject(self):
        """X·Esc. 측정 중에 닫으면 판정 스레드는 계속 재는데 안내만 사라지므로 막는다"""
        if self.measuring:
            self.note.setText("측정 중에는 닫을 수 없어요. 안내가 끝날 때까지 기다려 주세요.")
            self.note.show()
            return
        super().reject()

    def _start(self):
        name = self.name_edit.text().strip()
        if not name:
            self.guide.setText("이름을 입력해 주세요.")
            return
        self.measuring = True
        self.name_edit.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.start_requested.emit(name)

    def show_guide(self, lines: list[str], zone_step: bool):
        """판정 스레드가 보낸 안내 줄을 그대로 보여 준다 (남은 시간, 다시 하기 사유 등)"""
        if lines:
            self.guide.setText("\n".join(lines[:4]))
        self.next_btn.setVisible(zone_step)

    def finish(self, text: str, closable: bool = True):
        """한 단계가 끝남. closable=False면 영역 측정이 이어지므로 [닫기]를 아직 보이지 않는다"""
        self.measuring = not closable
        self.guide.setText(text)
        self.note.hide()
        self.next_btn.hide()
        self.start_btn.setText("닫기")
        self.start_btn.setEnabled(True)
        self.start_btn.setVisible(closable)
        self.start_btn.clicked.disconnect()
        self.start_btn.clicked.connect(self.accept)

    def fail(self, text: str):
        """카메라·모델 문제로 잴 수 없음. 기다리지 않게 바로 알리고 닫을 수 있게 한다"""
        self.finish(f"등록할 수 없어요: {text}\n노트북 카메라를 확인한 뒤 프로그램을 다시 실행해 주세요.")


class SeatMap(QWidget):
    """④ 강의실 지도: 앞쪽(강사·칠판) + 2×2 좌석, 내 자리 강조"""

    def __init__(self, seat: int, parent=None):
        super().__init__(parent)
        self.seat = seat
        self.setMinimumSize(360, 300)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(QColor("#97a0af"), 1))
        p.setBrush(QColor("#dfe1e6"))
        p.drawRoundedRect(QRectF(w * 0.15, 10, w * 0.7, 34), 6, 6)
        p.setPen(QColor("#172b4d"))
        p.drawText(QRectF(w * 0.15, 10, w * 0.7, 34), Qt.AlignmentFlag.AlignCenter, "앞 (강사 · 칠판)")
        cw, ch, top = w * 0.32, (h - 80) * 0.42, 64
        for n, (cx, cy) in SEAT_POS.items():
            r = QRectF(w * 0.14 + cx * (cw + w * 0.08), top + cy * (ch + 14), cw, ch)
            mine = n == self.seat
            p.setPen(QPen(QColor("#0747a6" if mine else "#97a0af"), 3 if mine else 1))
            p.setBrush(QColor("#deebff" if mine else "#ffffff"))
            p.drawRoundedRect(r, 8, 8)
            f = QFont()
            f.setPointSize(13 if mine else 11)
            f.setBold(mine)
            p.setFont(f)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{n}번\n내 자리" if mine else f"{n}번")
        p.end()


class SeatMapDialog(QDialog):
    def __init__(self, seat: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("강의실 지도")
        lay = QVBoxLayout(self)
        lay.addWidget(SeatMap(seat))
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        lay.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class FallAlertDialog(QDialog):
    """③ 쓰러짐 확인 요청: 15초 옆자리 / 30초 반 전체. [확인했어요] → fall_confirm (강사 화면에 기록)

    - 부모 없는 창으로 띄운다: 평소 화면을 내려 둔(최소화) 학생에게도 보이게.
    - 일부러 누른 클릭만 확인으로 친다: 창이 뜨면서 포커스를 가져가므로, 다른 창에서 치던 Space·Enter가
      [확인했어요]를 누르면 아무도 안 본 쓰러짐이 '확인됨'이 되어 단계 알림이 멈춘다.
      → 버튼은 포커스·기본 버튼이 되지 않고, 뜬 뒤 ARM_MS 동안은 눌리지 않는다. Esc로도 닫히지 않는다.
    - 소리는 app이 낸다 (gui/alert_sound.py).
    """
    confirmed = pyqtSignal(int, int)           # target_seat, event_id (Control이 안 보내면 0)

    def __init__(self, target_seat: int, stage: str, event_id: int = 0, parent=None):
        super().__init__(parent)
        self.target_seat, self.event_id = target_seat, event_id
        self.setWindowTitle("확인 요청")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet("QDialog{background:#ffebe6;} QLabel{color:#bf2600;}")
        self.title = QLabel()
        self.title.setStyleSheet("font-size:20px; font-weight:bold;")
        body = QLabel(f"{target_seat}번 자리 친구가 쓰러졌을 수 있어요.\n가까이 가서 괜찮은지 확인해 주세요.")
        body.setStyleSheet("font-size:15px;")
        self.btn = QPushButton("확인했어요")
        self.btn.setMinimumHeight(44)
        self.btn.setStyleSheet("font-size:16px; font-weight:bold;")
        self.btn.setAutoDefault(False)
        self.btn.setDefault(False)
        self.btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)      # Space·Enter로는 안 눌림, 마우스로만
        self.btn.clicked.connect(self._confirm)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(body)
        lay.addWidget(self.btn)
        self.set_stage(stage, event_id)

    def set_stage(self, stage: str, event_id: int = 0):
        """처음 뜰 때와 옆자리 → 반 전체로 올라갈 때. 다시 앞으로 나오므로 잠깐 다시 잠근다"""
        self.title.setText(f"⚠ {STAGE_KO.get(stage, '확인 요청')}")
        if event_id:
            self.event_id = event_id
        self.btn.setEnabled(False)
        QTimer.singleShot(ARM_MS, lambda: self.btn.setEnabled(True))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:          # Esc로 알림이 사라지지 않게 (X 버튼으로는 닫을 수 있음)
            return
        super().keyPressEvent(e)

    def _confirm(self):
        self.confirmed.emit(self.target_seat, self.event_id)
        self.accept()
