"""팬틸트 모듈이 쓰는 번호표.

예전에는 common/protocol.py(Control <-> Camera Controller TCP)에 있던 값이다.
팬틸트가 Control 안으로 들어오면서 TCP 메시지가 필요 없어져 이 모듈 안에 둔다.
Console 화면·DB에 같은 번호를 쓰므로 값은 바꾸지 않는다.
"""
from enum import IntEnum

# Control에 알리는 이벤트 이름 (PanTiltService on_event의 첫 인자)
EVENT_STATE = 'pan_tilt_state'        # fields: state, pan, tilt, gen, error
EVENT_PRESET_SAVED = 'preset_saved'   # fields: seat, pan, tilt


class Kind(IntEnum):
    """사건 종류. LED 색: 1 빨강, 2 파랑, 3 초록."""
    NONE = 0
    FALL = 1
    QUESTION = 2
    AWAY = 3


class TrackStatus(IntEnum):
    LOST = 0
    TRACKED = 1


class FsmState(IntEnum):
    INIT = 0
    IDLE = 1
    MOVE_TO_SEAT = 2
    TRACKING = 3
    SEARCHING = 4
    HOLD_SEAT = 5
    RETURN_HOME = 6
    SAFE = 7


class PanTiltError(IntEnum):
    """pan_tilt_state.error. 팀 확정 전 제안값."""
    NONE = 0
    SERIAL = 1             # Uno 통신 오류 (SAFE)
    NO_PRESET = 2          # 프리셋 없는 좌석 요청
    MOVE_TIMEOUT = 3       # 이동 출력 완료(D) 시간 초과
    INIT_TIMEOUT = 4       # Uno 초기화 응답 없음
    VISION_LOST = 5        # Vision 연결 끊김 (추적 중단)
