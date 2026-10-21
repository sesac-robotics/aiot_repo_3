"""전송용 상태 요약 (시험용): 매 프레임의 확정 상태(Monitor.state)를 전송 주기 동안 모아 bool 4개로 만든다

전송 직전 한 순간만 보면 주기 사이에 일어난 상태를 놓치므로, 주기 동안 한 번이라도 확정되면 True.
  drowsy / yawn / not_forward : 얼굴 지속 시간 필터에서 확정 (졸음 1.5초, 하품 1초, 전방 주시 미흡 2초)
  forward_lean                : 자세 앞으로 숙임 확정 (3초). 규칙 모드의 거북목·구부정도 여기에 포함
기준 자세 등록 중이거나 사람이 없으면 판정이 없으므로 모두 False.
"""
FIELDS = ("drowsy", "yawn", "not_forward", "forward_lean")
FACE_KEYS = ("drowsy", "yawn", "not_forward")
FORWARD_KEYS = {"forward_lean", "forward_head", "slouch"}       # 학습 모델(merged) / 규칙 모드 이름


class ConditionLatch:
    def __init__(self):
        self.reset()

    def reset(self):
        self._flags = dict.fromkeys(FIELDS, False)

    def update(self, state: dict):
        """state: Monitor.state = {"present", "calibrating", "face": (확정 얼굴 문제), "posture": (확정 자세 문제)}"""
        face = set(state.get("face", ()))
        for k in FACE_KEYS:
            self._flags[k] |= k in face
        self._flags["forward_lean"] |= bool(FORWARD_KEYS & set(state.get("posture", ())))

    def snapshot(self, reset=True) -> dict:
        """→ {"drowsy", "yawn", "not_forward", "forward_lean"}"""
        out = dict(self._flags)
        if reset:
            self.reset()
        return out


def to_summary(flags: dict) -> dict:
    """세부 4개 → control 의 condition_summary 형식 {"focus_low", "posture_bad"} (jarvis_control_service/CLAUDE.md)
    강사에게는 반 집계만 가므로(SR_006) 세부 항목은 학생 화면에만 쓰고 control 에는 보내지 않는다."""
    return {"focus_low": any(bool(flags.get(k)) for k in FACE_KEYS),
            "posture_bad": bool(flags.get("forward_lean"))}