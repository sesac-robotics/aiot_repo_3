"""본인 알림 공통 팝업: 화면 아래쪽에 5초 뜨고 사라지는 가벼운 권유 (화면 구성도 v3 학생 ②, SP-230)

- 문제가 확정된 순간(없다가 생김)에만 띄운다. 문구·분류(색)·우선순위·표시 시간·억제 간격은
  student_gui/messages.py 규칙 표를 따른다 (이 파일은 표를 읽기만 한다).
- 한 번에 하나만 표시한다. 여러 상황이 겹치면 우선순위가 가장 높은 것만 띄우고 나머지는 버린다.
  떠 있는 동안 더 높은 상황이 생기면 그것으로 바꾸고, 같거나 낮으면 버린다.
- 같은 종류는 실제로 화면에 띄운 순간부터 그 종류의 억제 간격 동안 다시 띄우지 않는다 (같은 알림 반복 제한).
  버린 알림은 띄운 것이 아니므로 반복 제한에 들어가지 않는다 (다음에 다시 생기면 뜰 수 있다).
- [알림 끄기]를 누르면 본인 알림만 끈다. 쓰러짐 확인 요청은 끌 수 없다 (별도 창).
- 띄울 때 로그를 남긴다 (반복 억제 확인용): "본인 알림: drowsy 졸린가요? ..."
- 판정 상태 말고 다른 곳(예: 장시간 같은 자세)에서는 notify(이름)로 부른다.
"""
import logging
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QLabel

from student_gui.messages import COLORS, priority, rule

log = logging.getLogger("student_gui")


class Notifier(QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self._muted = False
        self._last: dict[str, float] = {}         # 종류 → 마지막으로 화면에 띄운 시각
        self._prev: set[str] = set()              # 직전 프레임에 확정돼 있던 종류
        self._showing = None                      # 지금 떠 있는 종류
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._done)

    # ---- [알림 끄기] -----------------------------------------------------
    @property
    def muted(self):
        return self._muted

    @muted.setter
    def muted(self, on: bool):
        self._muted = bool(on)
        if self._muted:
            self._done()

    # ---- 입력 ------------------------------------------------------------
    def update_state(self, state: dict):
        """매 프레임 상태(Monitor.state)를 받아, 새로 확정된 문제 중 우선순위가 가장 높은 것만 알린다"""
        if not state.get("present") or state.get("calibrating"):
            self._prev = set()
            return
        now_set = {k for k in state.get("face", ()) if rule(k)}
        if state.get("posture"):
            now_set.add("posture")
        fresh = [k for k in now_set - self._prev if not self._cooling(k)]
        if fresh:
            self.notify(min(fresh, key=priority))
        self._prev = now_set

    def notify(self, kind: str) -> bool:
        """kind 알림을 띄운다. 끔·반복 억제·떠 있는 알림보다 낮은(같은) 우선순위면 버리고 False"""
        if self._muted or rule(kind) is None or self._cooling(kind):
            return False
        if self._showing is not None and priority(self._showing) <= priority(kind):
            return False
        self._show(kind)
        return True

    # ---- 표시 ------------------------------------------------------------
    def _cooling(self, kind):
        last = self._last.get(kind)
        return last is not None and time.monotonic() - last < rule(kind).cooldown_s

    def _show(self, kind):
        r = rule(kind)
        self._last[kind] = time.monotonic()
        self._showing = kind
        log.info("본인 알림: %s %s", kind, r.text)
        self.setStyleSheet(f"background:{COLORS[r.group]}; color:white; border-radius:10px;"
                           "padding:10px 16px; font-size:15px;")
        self.setText(r.text)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(int(r.show_s * 1000))

    def _done(self):
        self._timer.stop()
        self._showing = None
        self.hide()

    def _place(self):
        p = self.parentWidget()
        w = min(460, p.width() - 40)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move((p.width() - w) // 2, p.height() - self.height() - 80)