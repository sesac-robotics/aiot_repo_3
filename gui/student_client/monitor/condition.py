"""control 로 보내는 상태 요약 (SP-231): 매 프레임 확정 상태(Monitor.state)를 전송 주기 동안 모아 개별 6개 + 묶음 2개로 만든다

학생 GUI(student_gui/engine.py)와 터미널 실행(monitor/seat_link_main.py)이 같이 쓴다. 기준은 이 파일 하나뿐이다.
전송 직전 한 순간만 보면 주기 사이에 일어난 상태를 놓치므로, 주기 동안 한 번이라도 확정되면 True.
  얼굴 drowsy / yawn / not_forward   : 얼굴 지속 시간 필터에서 확정 (졸음 1.5초, 하품 1초, 전방 주시 미흡 2초)
  자세 forward_lean / head_tilt / shoulder_tilt : 자세 확정 (학생 GUI 30초). 규칙 모드의 거북목·구부정은 forward_lean
  묶음 focus_low = 얼굴 3개 중 하나라도, posture_bad = 자세 3개 중 하나라도 (서버가 개별 6개로 바꿀 때까지 함께 보냄)
기준 자세 등록 중이거나 사람이 없으면 판정이 없으므로 모두 False.
항목 이름 목록은 common/protocol.py 의 CONDITION_DETAIL_KEYS 와 같다 (서버도 같은 이름으로 받는다).
"""
FACE_KEYS = ("drowsy", "yawn", "not_forward")
POSTURE_KEYS = ("forward_lean", "head_tilt", "shoulder_tilt")
FIELDS = FACE_KEYS + POSTURE_KEYS
POSTURE_ALIAS = {"forward_head": "forward_lean", "slouch": "forward_lean"}   # 규칙 모드(--rule) 이름 → 전송 이름


class ConditionLatch:
    def __init__(self):
        self.reset()

    def reset(self):
        self._flags = dict.fromkeys(FIELDS, False)

    def update(self, state: dict):
        """state: Monitor.state = {"present", "calibrating", "face": (확정 얼굴 문제), "posture": (확정 자세 문제)}"""
        if not state.get("present") or state.get("calibrating"):
            return
        for k in state.get("face", ()):
            if k in self._flags:
                self._flags[k] = True
        for k in state.get("posture", ()):
            k = POSTURE_ALIAS.get(k, k)
            if k in self._flags:
                self._flags[k] = True

    def snapshot(self, reset=True) -> dict:
        """→ 개별 6개 {"drowsy", "yawn", "not_forward", "forward_lean", "head_tilt", "shoulder_tilt"}"""
        out = dict(self._flags)
        if reset:
            self.reset()
        return out


def to_summary(flags: dict) -> dict:
    """개별 6개 → control 의 condition_summary 값 (묶음 2개 + 개별 6개, 모두 true/false)"""
    detail = {k: bool(flags.get(k)) for k in FIELDS}
    return {"focus_low": any(detail[k] for k in FACE_KEYS),
            "posture_bad": any(detail[k] for k in POSTURE_KEYS),
            **detail}