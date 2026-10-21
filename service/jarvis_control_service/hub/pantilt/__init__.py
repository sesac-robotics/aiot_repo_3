"""팬틸트 카메라 제어 (Camera Controller). Control은 PanTiltService만 쓰면 된다.

    service.py        PanTiltService: 명령함 + 20ms 스레드 (Control이 부르는 입구)
    controller.py     FSM 8상태, 좌석 프리셋 이동, 지연 보상 P 제어, 재탐색, 프리셋 등록
    serial_link.py    Uno 시리얼 (IF-04 ASCII, seq·재전송·하트비트) + 모의 Uno
    preset_store.py   config/presets.json 읽기·검사·저장
    constants.py      사건 종류·FSM 상태·오류 번호

Uno 펌웨어는 hw/camera_controller/camera_controller.ino.
"""
from .constants import EVENT_PRESET_SAVED, EVENT_STATE, FsmState, Kind, PanTiltError
from .service import PanTiltService

__all__ = ['PanTiltService', 'Kind', 'FsmState', 'PanTiltError', 'EVENT_STATE', 'EVENT_PRESET_SAVED']
