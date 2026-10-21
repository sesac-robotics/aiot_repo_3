"""본인 알림 문구 표: 확정된 상태 이름 → (분류, 문구). 알림 동작은 student_gui/notifier.py

- 분류: focus 집중 (SP-220), posture 자세 (SP-226), rest 휴식·스트레칭 (SP-228). 분류마다 팝업 색이 다르다.
- 상태 이름은 판정 쪽 이름을 그대로 쓴다 (seat_common.face_judge, learning.labels).
- 새 알림은 이 표에 한 줄 추가하고, 판정 상태(Monitor.state)에 그 이름이 나오게 하거나 Notifier.notify(이름)로 부른다.
"""

MESSAGES = {
    # 집중 (얼굴)
    "drowsy": ("focus", "졸린가요? 잠깐 일어나 스트레칭해 볼까요?"),
    "yawn": ("focus", "피곤해 보여요. 물 한 잔 어때요?"),
    "not_forward": ("focus", "화면으로 다시 돌아와 볼까요?"),
    # 자세 (종류별 문구는 SP-226에서 나눈다)
    "posture": ("posture", "허리를 펴고 어깨를 내려 볼까요?"),
}

COLORS = {              # 분류 → 팝업 배경 (Qt 스타일시트는 #RGBA를 못 읽으므로 rgba())
    "focus": "rgba(172, 107, 8, 215)",
    "posture": "rgba(0, 82, 204, 210)",
    "rest": "rgba(0, 102, 68, 210)",
}


def message(kind: str):
    """상태 이름 → (분류, 문구). 표에 없으면 None"""
    return MESSAGES.get(kind)