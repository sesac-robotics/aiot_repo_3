# Camera Controller — Arduino Uno 펌웨어

팬틸트 서보 2개와 RGB LED를 PC 명령대로 움직이는 Uno 펌웨어다. **판단은 하지 않는다.**
좌석 이동·P 제어·FSM 같은 판단 로직은 Jarvis Control Service 안의
`service/jarvis_control_service/hub/pantilt/`에 있고, Uno와는 USB 시리얼(IF-04)로만 통신한다.

```
Jarvis Control Service (hub/pantilt) ──USB Serial 9600bps ASCII──▶ Arduino Uno ──▶ Pan·Tilt 서보, RGB LED
```

## 배선

| 장치 | Uno 핀 | 비고 |
|---|---|---|
| Pan 서보 신호 | D9 | 좌우 |
| Tilt 서보 신호 | D10 | 상하 |
| RGB LED R / G / B | D3 / D5 / D6 | 색마다 저항. 공통 애노드면 `COMMON_ANODE = true` |
| 서보 전원 | 외부 전원 | Uno 5V로 서보를 구동하지 않는다 |
| GND | 외부 전원·서보·Uno 공통 | |

## 업로드

1. Arduino IDE에서 `camera_controller.ino`를 연다 (폴더 이름 `camera_controller`와 파일 이름이 같아야 한다).
2. 보드 Arduino Uno, 포트 선택 후 업로드.
3. **업로드 후 IDE 시리얼 모니터는 닫는다.** 열려 있으면 Control이 포트를 열지 못한다.

## 가동 범위 (펌웨어 고정)

| 축 | 범위 | 근거 |
|---|---|---|
| Pan | 0° ~ 180° | 전 구간 구동 확인 |
| Tilt | 30° ~ 140° | 140° 초과 시 Pan 서보 전선 간섭으로 떨림, 30°는 운용상 하한 |

HOME 81° / 90°. 범위를 바꾸면 `pantilt/preset_store.py`의 `FW_LIMITS`와 `presets.json`의 `limits`도 함께 바꾼다.

## 시리얼 명령 (IF-04)

| PC → Uno | 의미 | 응답 |
|---|---|---|
| `C,seq,pMin,pMax,tMin,tMax,homePan,homeTilt` | 범위·HOME 설정 | `K,seq` |
| `H,seq` | HOME (최초 H에서 서보 출력 시작) | `K` → `D` |
| `S,seq,pan,tilt` | 좌석·재탐색 이동 (20ms/1°) | `K` → `D` |
| `T,seq,pan,tilt` | 추적 목표 (40ms/1°) | `K` |
| `X,seq` | 정지 | `K` |
| `L,seq,R/G/B/O` | LED 빨강/초록/파랑/끔 | `K` |
| `B` | 하트비트 (PC가 1초마다) | 없음 |

Uno → PC: `R` 부팅, `K` 접수, `D` 출력 완료, `E,seq,code` 오류(1 형식, 2 범위, 3 설정 전, 4 길이, 5 알 수 없는 명령, 9 통신 시간 초과).
3초 동안 명령·하트비트가 없으면 현재 각도에서 멈추고 빨강 LED를 0.5초마다 깜빡인다.

## 단독 확인

- 시리얼 모니터(9600bps, 줄 끝 "새 줄"): `C,1,0,180,30,140,81,90` → `H,2` → `S,3,100,85` → `L,4,R`
- Python으로 확인: `service/jarvis_control_service/tools/manual_event_test.py` (1 쓰러짐 / 2 질문 / 3 자리비움)
