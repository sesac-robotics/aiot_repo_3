# hub/pantilt — 팬틸트 카메라 제어 (Camera Controller)

Jarvis Control Service 안에서 팬틸트 카메라를 움직이는 모듈이다.
Control이 사건을 확정하면 `PanTiltService`의 메서드를 부르고, 이 모듈이 좌석 이동·P 제어 추적·LED를 처리해
Arduino Uno에 시리얼 명령(IF-04)을 보낸다.

```
Vision ──track_obs──▶ Control ──pantilt.preset()/track()/home()──▶ PanTiltService ──Serial──▶ Uno ──▶ 서보·LED
                        ▲                                              │
                        └──────────── on_event('pan_tilt_state', …) ◀──┘
```

## 파일

| 파일 | 역할 |
|---|---|
| `service.py` | **PanTiltService**: Control이 부르는 입구. 명령함(queue) + 20ms 전용 스레드, Uno 재연결 |
| `controller.py` | FSM 8상태, 좌석 프리셋 이동, 지연 보상 P 제어, 재탐색, jog·프리셋 저장 |
| `serial_link.py` | Uno 시리얼(seq·재전송·하트비트), 모의 Uno(SimDevice), Uno 포트 자동 찾기 |
| `preset_store.py` | `config/presets.json` 읽기·검사·저장 |
| `constants.py` | 사건 종류(Kind), FSM 상태, 오류 번호, 이벤트 이름 |

설정 파일: `service/jarvis_control_service/config/presets.json` · 펌웨어: `hw/camera_controller/camera_controller.ino`
수동 시험: `service/jarvis_control_service/tools/manual_event_test.py`

## Control에서 쓰는 법

```python
from hub.pantilt import PanTiltService      # hub 패키지 안에서는 from .pantilt import PanTiltService

pantilt = PanTiltService(port='auto', on_event=on_pantilt_event)   # Control 시작 시 한 번
```

| Control에서 일어나는 일 | 부를 메서드 |
|---|---|
| 사건 확정·우선순위 선점·대기열의 다음 사건 (gen+1) | `pantilt.preset(gen, seat, kind)` — kind 1 쓰러짐, 2 질문, 3 자리비움 |
| Vision `track_obs{gen, found, cx, cy}` 수신 | `pantilt.track(gen, cx, cy, captured_at, frame_id, found)` |
| 남은 사건 없음 | `pantilt.home(gen)` |
| Console `pan_tilt_jog{dpan, dtilt}` | `pantilt.jog(dpan, dtilt)` |
| Console `preset_save{seat}` | `pantilt.save_preset(seat)` |
| Vision 노드 접속 / 끊김 | `pantilt.set_vision_online(True / False)` |
| Console 표시·디버그 | `pantilt.status()`, `pantilt.presets()` |
| Control 종료 | `pantilt.close()` |

- 메서드는 명령함에 넣고 바로 돌아온다. 시리얼이 느리거나 끊겨도 Control은 멈추지 않는다.
- `cx, cy`는 **팬틸트 카메라(cam2) 영상** 기준 화면 비율(0~1, 중앙 0.5)이어야 한다. cam1(고정) 좌표를 넣으면 안 된다.
- `captured_at`은 cam2 Streamer가 찍은 촬영 시각(Unix 초)을 **바꾸지 않고** 넘긴다. 300ms가 지난 좌표는 버린다.
- `gen`이 현재 사건과 다른 좌표는 버린다. 같은 gen·좌석·종류로 `preset()`을 다시 불러도 이동을 재시작하지 않는다.

## on_event (팬틸트 → Control)

`on_event(event, fields)`는 **팬틸트 스레드에서** 불린다. Control이 asyncio를 쓰면
`loop.call_soon_threadsafe(...)`로, 스레드를 쓰면 queue로 넘겨서 처리한다. 콜백에서 오류가 나도 팬틸트는 계속 돈다.

| event | fields | 언제 |
|---|---|---|
| `pan_tilt_state` | state, pan, tilt, gen, error | FSM 상태가 바뀔 때, 시작 직후, 오류 시 |
| `preset_saved` | seat, pan, tilt | `save_preset()` 후, 시작 직후 저장된 좌석마다 (DB·Console has_preset 동기화) |

state: 0 INIT · 1 IDLE · 2 MOVE_TO_SEAT · 3 TRACKING · 4 SEARCHING · 5 HOLD_SEAT · 6 RETURN_HOME · 7 SAFE
error(제안): 0 없음 · 1 시리얼 오류 · 2 프리셋 없음 · 3 이동 시간 초과 · 4 초기화 실패 · 5 Vision 끊김

## 설정 (제안)

시리얼 포트를 `config/control.yaml`에 두면 Control 설정과 같이 관리할 수 있다.

```yaml
pantilt:
  port: auto            # 또는 /dev/ttyACM0, COM3
  presets: config/presets.json
```

## 필요 패키지

`pyserial` (`python -m pip install pyserial`). Linux에서 포트 권한 오류가 나면 `sudo usermod -aG dialout $USER` 후 다시 로그인.
