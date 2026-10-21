"""본인 알림: 화면 아래쪽에 5초 뜨고 사라지는 가벼운 권유 (화면 구성도 v3 학생 ②)

- 같은 종류는 COOLDOWN_S 동안 다시 띄우지 않는다 (같은 알림 반복 제한)
- [알림 끄기]를 누르면 본인 알림만 끈다. 쓰러짐 확인 요청은 끌 수 없다.
- 문제가 확정된 순간(없다가 생김)에만 띄운다.
"""
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

SHOW_S = 5
COOLDOWN_S = 120

MESSAGES = {            # 확정된 문제 → 권유 문구 (판정 이름은 seat_common / posture_ensemble 기준)
    "drowsy": "졸린가요? 잠깐 일어나 스트레칭해 볼까요?",
    "yawn": "피곤해 보여요. 물 한 잔 어때요?",
    "not_forward": "화면으로 다시 돌아와 볼까요?",
    "posture": "허리를 펴고 어깨를 내려 볼까요?",
}


class Notifier(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.muted = False
        self._last: dict[str, float] = {}
        self._prev: set[str] = set()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        # 반투명 회색. Qt 스타일시트는 #RGBA(예: #333a)를 못 읽고, 하나라도 틀리면 전체를 버린다
        self.setStyleSheet("background:rgba(51, 51, 51, 170); color:white; border-radius:10px;"
                           "padding:10px 16px; font-size:15px;")
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def update_state(self, state: dict):
        """매 프레임 상태를 받아, 새로 확정된 문제만 알린다"""
        if not state.get("present") or state.get("calibrating"):
            self._prev = set()
            return
        now_set = set(state.get("face", ())) & set(MESSAGES)
        if state.get("posture"):
            now_set.add("posture")
        for kind in now_set - self._prev:
            self._maybe_show(kind)
        self._prev = now_set

    def _maybe_show(self, kind):
        now = time.monotonic()
        if self.muted or now - self._last.get(kind, -COOLDOWN_S) < COOLDOWN_S:
            return
        self._last[kind] = now
        self.setText(MESSAGES[kind])
        self._place()
        self.show()
        self.raise_()
        self._timer.start(SHOW_S * 1000)

    def _place(self):
        p = self.parentWidget()
        w = min(460, p.width() - 40)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move((p.width() - w) // 2, p.height() - self.height() - 80)
