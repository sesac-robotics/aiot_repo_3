"""본인 알림 규칙 표: 확정된 상태 이름 → (분류, 문구, 표시 시간, 억제 간격). 알림 동작은 student_gui/notifier.py

- 분류: focus 집중 (SP-220), posture 자세 (SP-226), rest 휴식·스트레칭 (SP-228). 분류마다 팝업 색이 다르다.
- 표시 시간(show_s): 팝업이 떠 있는 초. 억제 간격(cooldown_s): 같은 알림을 띄운 뒤 다시 띄우지 않는 초.
  확정 기준(졸음 1.5초 등)은 판정 쪽(seat_common.face_judge, 자세 확정 시간)이 정하고, 여기는 표시 규칙만 둔다.
- 상태 이름은 판정 쪽 이름을 그대로 쓴다 (seat_common.face_judge, learning.labels).
- 새 알림은 이 표에 한 줄 추가하고(우선순위도), 판정 상태(Monitor.state)에 그 이름이 나오게 하거나
  Notifier.notify(이름)로 부른다.
- 여러 상황이 겹치면 PRIORITY에서 앞에 있는 것 하나만 표시한다 (SP-230).
"""
from typing import NamedTuple


class Rule(NamedTuple):
    group: str              # focus / posture / rest
    text: str               # 팝업 문구
    show_s: float = 5.0     # 표시 시간 (초)
    cooldown_s: float = 120.0   # 억제 간격 (초)


RULES = {
    # 집중 (얼굴, SP-220)
    "drowsy": Rule("focus", "졸린가요? 눈을 크게 깜빡이고 숨을 한 번 크게 쉬어 볼까요?", 5, 120),
    "yawn": Rule("focus", "피곤해 보여요. 물 한 잔 어때요?", 5, 120),
    "not_forward": Rule("focus", "화면으로 다시 돌아와 볼까요?", 5, 60),
    # 자세 (SP-226). 학습 모델: forward_lean·head_tilt·shoulder_tilt / 규칙 모드(--rule): forward_head·slouch도 나옴
    "forward_lean": Rule("posture", "몸이 화면 쪽으로 숙여졌어요. 허리를 펴고 등을 의자에 붙여 볼까요?", 5, 120),
    "head_tilt": Rule("posture", "고개가 한쪽으로 기울었어요. 고개를 바로 세워 볼까요?", 5, 120),
    "shoulder_tilt": Rule("posture", "어깨 높이가 달라요. 양쪽 어깨를 나란히 내려 볼까요?", 5, 120),
}
RULES["forward_head"] = RULES["slouch"] = RULES["forward_lean"]     # 규칙 모드 이름 → 앞으로 숙임 문구

PRIORITY = ("drowsy", "not_forward", "yawn",                  # 앞일수록 먼저. 표에 없으면 맨 뒤
            "forward_lean", "forward_head", "slouch", "head_tilt", "shoulder_tilt")

COLORS = {              # 분류 → 팝업 배경 (Qt 스타일시트는 #RGBA를 못 읽으므로 rgba())
    "focus": "rgba(172, 107, 8, 215)",
    "posture": "rgba(0, 82, 204, 210)",
    "rest": "rgba(0, 102, 68, 210)",
}


def rule(kind: str):
    """상태 이름 → Rule. 표에 없으면 None"""
    return RULES.get(kind)


def priority(kind: str) -> int:
    """작을수록 우선 (PRIORITY 순서)"""
    return PRIORITY.index(kind) if kind in PRIORITY else len(PRIORITY)