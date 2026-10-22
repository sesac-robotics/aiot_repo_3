# Control Server (Jarvis Control Service)

이 폴더에서 작업할 때 따르는 설계 기준.
원본: Confluence "Control Server 상세 설계" v0.1 (2026-10-19) — https://skypol-sesac.atlassian.net/wiki/x/BABt
연결 구조: Confluence "System Architecture" SW Architecture 그림 (2026-10-20 판) — https://skypol-sesac.atlassian.net/wiki/spaces/1/pages/360667/System+Architecture
원본이 바뀌면 이 파일도 다시 맞춘다. 원본과 다르면 **원본이 우선**이다.

## 핵심 원칙

- **기존 코드(지우님 구현)를 기반으로 하고, 비어 있는 이벤트 관리만 추가한다. 새로 짜지 않는다.**
- 모든 프로그램(Vision, 팬틸트 제어, 강사 화면, 학생 화면)은 TCP 노드로 Control에 붙고, 통신은 `NodeClient` 하나를 같이 쓴다.
- **카메라 영상은 Control을 거쳐 Vision Service로 간다** (10/21 설계 변경). 두 Camera Streamer(팬틸트·고정) → Control (UDP) → Vision Service (UDP).
  Vision의 판정 결과·사건은 TCP(`NodeClient`)로 Control에 온다.
- 영상 중계는 판단이 아니다. `event_manager`를 거치지 않고 DB에도 기록하지 않는다. 중계 때문에 asyncio 루프(0.1초 `tick`)가 막히면 안 된다.
  예외는 **이벤트 녹화**(`event_clip`): 녹화 시작·종료 시점은 `event_manager`가 정하고(`to="recorder"`), cam2 프레임 디코딩·MP4 쓰기는 `hub/clip_recorder.py` 작업 스레드가 한다.
- **판단(우선순위, 선점, 대기열, 단계 알림, 만료, 추적 대상)은 `event_manager.py` 한 곳에서만 한다.** 화면도 Vision도 판단하지 않는다.
- **DB에 쓰는 곳은 `db.py`뿐이다.** 다른 도구는 읽기만 한다. DB는 SQLite 파일 하나.
- LLM 상황 설명: Gemini API가 기본, Ollama가 백업.

## 폴더 구조

```
aiot_repo_3/
├─ common/                                    모든 노드 공용 (강사님 확인 필요한 추가 폴더)
│   ├─ protocol.py                            노드·메시지 종류·좌석의 유일한 기준
│   ├─ node_client.py                         NodeClient
│   └─ config.py                              config/common.yaml 읽기 (load_config)
├─ config/common.yaml                         control_host, control_port (모든 노드)
├─ gui/instructor_console/run_console.py      instructor_console 노드 (강사 대시보드)
├─ gui/student_client/run_client.py           student_client_N 노드 (--seat N)
├─ service/jarvis_control_service/            control 서버 (이 폴더)
│   ├─ hub/hub.py, event_manager.py, db.py
│   ├─ hub/control_config.py                  control 전용: control.yaml 읽기, DB·스키마·녹화 경로
│   ├─ hub/video_relay.py                     UDP 영상 중계 (cam2 프레임은 녹화로도 넘김)
│   ├─ hub/clip_recorder.py                   이벤트 녹화 (cam2 → MP4, 작업 스레드)
│   ├─ config/control.yaml                    좌석 각도·옆자리·타이머 (control 만)
│   ├─ db/hub.db                              SQLite 파일
│   ├─ tools/
│   └─ run_hub.py, run_client.py
├─ service/jarvis_DB/create.sql               DB 스키마 원본 (db.py 가 시작할 때 실행)
├─ service/jarvis_DB/clips/{event_id}.mp4     이벤트 녹화 파일 (git 제외, DB event_clip.path = "clips/{event_id}.mp4")
├─ service/jarvis_vision_service/run_assistant.py   vision_service 노드
├─ hw/camera_controller/camera_controller.ino Uno 펌웨어 (팬틸트는 Control 안의 hub/pantilt 가 USB 시리얼로 직접 움직임)
└─ hw/camera_streamer/                        UDP 영상 송출 → Control (TCP 노드 아님)
    ├─ run_fixed_camera.py                    고정 카메라(내장 웹캠, cam1)
    ├─ run_pantilt_camera.py                  팬틸트 카메라(USB, cam2)
    └─ streamer.py                            두 실행 파일이 같이 쓰는 송출 코드
```

- `common/`은 저장소 최상위에 있다. 각 폴더의 run 파일은 저장소 최상위(`aiot_repo_3/`)를 `sys.path` 맨 앞에 넣고 `from common... import`한다.
  이 폴더 안에서는 `hub/__init__.py`, `tools/__init__.py`가 그 경로를 넣는다.
- control 실행은 여전히 이 폴더에서 한다 (`python3 -m hub`, `python3 run_hub.py`).
- 모든 노드가 같이 쓰는 설정은 최상위 `config/common.yaml`, control 전용 설정은 이 폴더의 `config/control.yaml`. control 전용 경로·설정을 `common/`에 넣지 않는다.
- `run_client.py`(이 폴더)는 노드 템플릿 실행용. 학생 화면은 `gui/student_client/run_client.py`.

아직 하지 않은 이동 (확정되면):

| 현재 | 목표 |
| --- | --- |
| `hub/hub.py`, `hub/db.py`, `hub/event_manager.py`, `hub/control_config.py` | `hub_server.py`, `db.py`, `event_manager.py` … (이 폴더 바로 아래) |
| `tools/test_event_manager.py` | (위치 미정) |

## 실행 / 시험

```bash
python3 run_hub.py                                  # control 서버 (또는 python3 -m hub -v)
python3 -m unittest tools.test_event_manager -v     # event_manager 단위 테스트 (가짜 시계)
python3 -m unittest tools.test_protocol -v          # 바이너리 프레임 단위 테스트 (왕복, 스트림 쪼개짐, CRC, 깨진 바이트)
python3 -m unittest tools.test_ask_tools -v         # [질의] 도구 6개 (검색 조건, 통계, 인자 오류, 학생 이름 안 나감)
python3 -m unittest tools.test_clip_recorder -v     # 이벤트 녹화: 가짜 cam2 JPEG → MP4, event_clip 상태 1→2→3, clip_info, 재시작
python3 -m tools.fake_node --server 127.0.0.1       # 통신 시나리오: 질문 → 쓰러짐 선점 → confirm/resolve
python3 -m tools.fake_node --server 127.0.0.1 --fall-wait 31   # 15초/30초 fall_alert 까지
```

- 로그는 화면과 `logs/hub.log`(5MB × 5개 순환, git 제외)에 같이 남는다. `--log-file ""`이면 화면에만.
- 학생 노드 수신 확인: `grep hub.student logs/hub.log`
  - `수신 OK student_client_N condition_summary ...` / `수신 OK ... fall_confirm` — 받아서 처리함
  - `수신 실패 ...` — 형식이 틀려 버림 (이유가 같이 찍힘)
  - `전송 OK -> student_client_N fall_alert ...` — 학생에게 알림을 보냄
  - `... condition_summary 가 30초째 없음` — 접속은 살아 있는데 판정 결과가 안 옴 (학생 프로그램 확인)
  - `학생 수신 현황 | 1번 OK 최근 6건 ... | 2번 미접속 ...` — 1분마다 좌석별 요약
- `SchemaMismatch` 오류가 나면 `db/hub.db`, `db/hub.db-wal`, `db/hub.db-shm`을 지우고 다시 실행한다. 스키마를 바꾸면 `db.py`의 `SCHEMA_VERSION`을 올린다.

## 코드 구조 (판단과 실행의 분리)

- `event_manager.EventManager`는 네트워크도 DB도 모른다. 입력(`on_incident`, `on_confirm`, `on_resolve`, `tick(now)` …)을 받으면 할 일 `Effects(sends, saves, logs)`를 돌려준다.
- `hub.HubServer.apply()`가 그것을 `db.apply()`로 기록하고 `send()`로 보낸다. hub에는 판단을 넣지 않는다.
- 재시작하면 `help_event`에서 열린 이벤트를 읽어 `EventManager`를 복구한다.

## 설계 문서에 없어서 구현에서 정한 것 (바꿔도 됨, 팀에 공유할 것)

- 질문 만료는 **강사가 confirm하지 않은 질문만** 해당된다.
- 자리비움 미복귀 재알림은 한 번만 하고, `event_update`의 `overdue=true`로 알린다.
- `risen`(일어남), `overdue`는 화면 표시용이라 `event_update`에만 싣고 DB에는 없다. `hand_down`은 v2부터 DB에 저장한다.
- 쓰러짐이 `end`(일어남)여도 confirm 전까지 단계 알림은 계속된다.
- `fall_alert_cancel`은 그 단계에서 알림을 받은 학생에게만 보낸다(neighbor면 옆자리, all이면 전체).
- `track_offset`은 메시지의 `seat`이 지금 active 좌석과 같아야 처리한다. Vision은 `seat`을 꼭 채운다.
- nudge 각도 = `dx·nudge_gain`, `dy·nudge_gain`. 방향이 반대면 `nudge_gain`을 음수로 한다.
- `class_condition`은 최근 30초 안에 보고한 학생만으로 비율을 내고, `reporting`(보고한 수)도 같이 보낸다.
- 다시 접속한 `vision_service`에는 지금 추적 대상(`track_target`)을 다시 보낸다.
- `NodeClient`는 끊긴 동안 사건 외에 `confirm`·`resolve`·`fall_confirm`도 모아 두었다가 보낸다.
- [녹화] 열린 이벤트는 **active든 waiting이든 모두** 확정~종료 구간을 녹화한다 (Data Structure v2 `started_at`=확정, `ended_at`=종료 그대로).
  팬틸트는 active 좌석만 보므로 waiting 구간 영상은 다른 좌석이 찍힌다. active 구간만 남길지는 팀 논의.
- [녹화] 같은 cam2 영상을 이벤트마다 따로 파일로 쓴다 (동시 이벤트 2건이면 파일 2개).
- [녹화] 녹화 시각은 Control 시계(프레임 수신 시각). 10fps 고정, 15fps 입력은 솎고 2초 이하 끊김은 마지막 장면 반복, 그 이상은 이어 붙인다.
- [녹화] cam2 영상을 한 장도 못 받은 녹화(카메라 꺼짐)는 파일을 만들지 않고 status 3(삭제됨)으로 남긴다.
- [녹화] 코덱은 `avc1`(H.264)을 먼저 시도하고 안 되면 `mp4v`. pip OpenCV는 보통 `mp4v`가 된다.
- [녹화] `event_clip.path`는 `service/jarvis_DB` 기준 상대 경로. `clips.dir`을 그 밖으로 바꾸면 절대 경로가 들어간다.
- [녹화] Control이 강제 종료(kill)되면 녹화 중이던 MP4는 끝이 안 써져 재생이 안 될 수 있다. Ctrl+C 종료는 파일을 닫고 status 2로 저장한다.

## 10/19 결정 사항

- D1 고정 카메라는 영상 송출만. 두 카메라 모두 `vision_service`(Vision B)가 판정.
- D2 Vision A는 옵션.
- D3 LLM: Gemini 기본, Ollama 백업.
- D4 Vision은 TCP 노드로 사건을 보낸다 (SA와 다름, 아래 참고).
- D6 강사의 확인 = `confirm`, 완료 = `resolve`. 수신 확인은 `ack` → `rx_ack`.
- D7 학습 자세는 좌석별 사건이 아니라 반 집계(`condition_summary`)로만.
- D8 좌석은 1~4번. 5번(매트) 없음.
- D9 하트비트는 `messages`에 기록하지 않음. 타이머는 Control 시계 기준. asyncio 메인 루프가 0.1초마다 `tick` 호출.

## SA 그림과의 관계

10/20 SW Architecture 그림 기준 연결:

| 구간 | 그림 | 용도 |
| --- | --- | --- |
| Camera Streamer(팬틸트·고정) → Control | UDP | 영상 |
| Control → Vision AI Service | UDP / TCP | 영상 중계(UDP) + 메시지(TCP) |
| Vision AI Service → Control | TCP | 사건·`track_offset`·`explain_text`·하트비트 |
| Vision AI Service → LLM API (Gemini) | HTTP | 상황 설명 |
| Control ↔ Instructor Console | TCP/UDP | 메시지(TCP). UDP 용도는 미정 (아래 5번) |
| Control ↔ Student Client | TCP | 메시지 |
| Control → Camera Controller (팬틸트 보드) | Serial | 팬틸트 명령 |
| Control ↔ Jarvis DB | - | Control만 접근 |

그림과 맞춰진 것 (예전 DEV 항목 정리):

- DEV-01 (해소) Control ↔ Vision 메시지는 TCP(`NodeClient`). 그림도 HTTP가 아니라 TCP.
- DEV-02 (폐지, 10/21) 예전엔 Camera Streamer → Vision 직접이었으나, 이제 그림대로 Control을 거친다.
- DEV-04 (일치) DB는 SQLite 파일, Control만 접근.

그림과 아직 다른 점:

- DEV-03 Control → USB Serial → Arduino Uno. 팬틸트는 TCP 노드가 아니라 Control 프로세스 안의 `hub/pantilt`(PanTiltService)다. 시리얼 포트는 Control만 잡는다. (예전 `camera_controller` TCP 노드·`run_controller.py`는 없앰)

## 이름 규칙 (소문자_밑줄)

노드 (`protocol.VALID_NODES`):

| 노드 | 실행 파일 | 비고 |
| --- | --- | --- |
| `control` | run_hub.py | hub가 보내는 메시지의 node 값 |
| `vision_service` | run_assistant.py | 기존 fixedcam/tracker 대체 |
| `camera_controller` | (없음) | 예전 팬틸트 TCP 노드. 지금은 Control 안의 `hub/pantilt` (노드 코드 2는 DB 장비 '팬틸트(가상)'로 남김) |
| `instructor_console` | run_console.py | 신규, 메인 대시보드 |
| `student_client_1` ~ `student_client_4` | run_client.py | 기존 student1~4 |

카메라 스트리머는 TCP 노드가 아니다 (UDP로 Control에 영상만 보낸다). 카메라 상태는 지금은 `vision_service` 하트비트에 `cam1_fps`, `cam2_fps`로 실린다.
영상이 Control을 거치게 되었으므로 Control이 직접 받은 fps로 바꿀지는 미정 (아래 7번).

사건 종류:

| 기존 | 변경 | 우선순위 |
| --- | --- | --- |
| `fall` | `fall` | 1 |
| `hand_raise` | `question` | 2 |
| `leave_seat` | `away` | 3 |
| `drowsy`, `posture` | 삭제 → `condition_summary`(집계) | - |

공통 메시지 형식 — **TCP 위의 바이너리 프레임 v3** (10/21 변경, 기준은 `common/protocol.py` 맨 위 설명):

| 바이트 | 필드 | 형식 | 내용 |
| --- | --- | --- | --- |
| 0-1 | magic | 2s | `0xAA 0x55` |
| 2 | version | uint8 | 3 (v1 = 줄 단위 JSON 폐기. 2번은 폐기된 student_client 시험 패킷이 써서 건너뜀) |
| 3 | node | uint8 | `NODE_CODES`: control 0 · vision 1 · camera 2 · console 3 · student 11~14 (DB `device.node`와 같음) |
| 4 | type | uint8 | `TYPE_CODES`: 공통 0x0_ · 사건 0x1_ · vision 0x2_ · camera 0x3_ · console 0x4_ · student 0x5_ |
| 5 | seat | uint8 | 1~4, 0 = 없음 |
| 6-9 | seq | uint32 | |
| 10-17 | ts | float64 | 보낸 PC 시각 |
| 18-21 | conf | float32 | |
| 22-25 | length | uint32 | 값 바이트 수 (최대 1 MiB) |
| 26-… | payload | TLV | value dict (None/bool/int64/float64/str/list/dict 태그 인코딩) |
| 끝 2 | crc | uint16 | CRC-16/CCITT (version ~ payload) |

- 빅엔디언. 받는 쪽은 `FrameReader`로 시작 표시를 찾아 자르고, 헤더(버전·코드·길이)나 CRC가 틀리면 1바이트씩 밀어 다시 찾는다 (연결은 유지).
- `encode()`/`parse_message()`가 주고받는 dict는 예전과 같다. 노드 코드와 `event_manager`는 형식을 모른다:
  `{"v":3, "node":"vision_service", "seq":1042, "type":"question", "seat":2, "ts":1760500000.123, "value":{"state":"start"}, "conf":0.81}`
- 새 노드·메시지 종류를 추가할 때는 `NODE_CODES`/`TYPE_CODES`에도 번호를 준다 (안 맞으면 import 할 때 assert 로 멈춤).

- `ts`는 보낸 PC의 시계. Control은 받는 순간 `rx_ts`(Control 시계)를 붙이고 **모든 판단은 `rx_ts`로 한다.**
- Interface Specification(IF-01~05)과 다르면 코드가 기준이다.

## 메시지 목록

영상 (UDP, 바이너리 프레임 v3 아님):

| 경로 | 내용 |
| --- | --- |
| `camera_streamer`(cam1 팬틸트, cam2 고정) → control | 카메라 영상 |
| control → `vision_service` | 받은 영상을 그대로 넘김 (중계) |

포트·패킷 형식·재인코딩 여부는 미정 (아래 8번). 정해지기 전에는 기존 streamer 패킷을 바꾸지 않고 그대로 넘기는 것을 기본으로 한다.

vision_service ↔ control:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↑ | `fall`/`question`/`away` | `{"state":"start"\|"end"}` | 판정 확정/해제 순간 한 번 (유지 시간은 Vision이 지킴) |
| ↑ | `track_offset` | `{"found":true,"dx":0.12,"dy":-0.05}` | 추적 중 10~15회/초. `messages`에 기록 안 함 |
| ↑ | `explain_text` | `{"event_id","text","mode":"gemini"\|"ollama"\|"default","seq","situation"}` | 설명 요청 응답. `seq`가 마지막 요청과 다르면 버림 (없으면 받음) |
| ↑ | `heartbeat` | `{"fps","cpu","cam1_fps","cam2_fps"}` | 2초마다 |
| ↓ | `track_target` | `{"seat":2}` 또는 `{"seat":null}` | 추적 대상 변경 시 |
| ↓ | `explain_request` | `{"event_id","kind","seat","situation","seq","status","queue_pos","elapsed_s","waiting_count","alert_stage","confirmed"}` (+선점 시 `preempted_kind`,`preempted_seat`) | [알림문구] 상황이 바뀔 때마다 (아래 표, 비동기) |
| ↓ | `ask_request` | `{"ask_id","text","now"}` | [질의] 강사 질문을 넘김 (`now` = control 지역 시각) |
| ↑ | `tool_call` | `{"ask_id","call_id","name","args"}` | [질의] LLM 이 고른 도구 실행 요청 |
| ↓ | `tool_result` | `{"ask_id","call_id","name","result"}` | [질의] `hub/ask_tools.py` 실행 결과 (오류도 `{"error"}` 로) |
| ↑ | `ask_answer` | `{"ask_id","text","mode":"gemini"\|"ollama"\|"rule"\|"default","tools":[{name,args,summary}],"took_s","fallback"}` | [질의] 최종 답 → 물어본 Console 로 그대로 |
| ↓ | `report_write` | `{"report_id","data","now"}` | [리포트] 오늘 숫자를 주고 문장 작성 요청 |
| ↑ | `report_text` | `{"report_id","summary","suggestions":[...],"mode","took_s","fallback"}` | [리포트] 요약 2~3문장 + 살펴볼 점 0~3개 |

팬틸트: TCP 메시지가 아니다. `event_manager`의 `to="pantilt"` 할 일을 hub가 `PanTiltService` 메서드(preset·home·track·jog·save)로 부르고,
팬틸트 알림(`pan_tilt_state`, `preset_saved`)은 이벤트 루프로 넘겨 받는다 (`hub/pantilt/README.md`).
`protocol.py`의 `pan_tilt`·`pan_tilt_state`는 예전 노드용으로 남아 있을 뿐 쓰지 않는다.

control ↔ instructor_console:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↓ | `event_update` | help_event 한 행 전체 | 이벤트 생성/변경마다 |
| ↓ | `queue_state` | `{"active":17,"waiting":[18,15]}` | 대기열 변경 시 |
| ↓ | `device_update` | `{"node","online","note"}` | 장비 상태 변경 시 |
| ↓ | `class_condition` | `{"focus_low_ratio","posture_bad_ratio"}` | 10초마다 (반 평균만) |
| ↓ | `snapshot` | `{events, queue, devices, seats}` 열린 이벤트 + 장비 상태 + 좌석(옆자리·학생 이름) | Console 접속/재접속 시 |
| ↓ | `seat_info` | `{seat, label, neighbors, has_preset, student_name, registered_at}` | 학생 [자리 등록] 시 |
| ↓ | `preset_saved` | `{seat, pan, tilt}` | 팬틸트 각도 저장 후 |
| ↑ | `confirm` | `{"event_id":17}` | 강사 "확인" → 단계 알림 멈춤 |
| ↑ | `resolve` | `{"event_id":17}` | 강사 "완료" → 이벤트 종료, 다음 대상 |
| ↑ | `dismiss` | `{"event_id":17}` | 강사 "오감지" → `close_reason=false_alarm`, 다음 대상 |
| ↑ | `pan_tilt_jog` / `preset_save` | `{dpan, dtilt}` / `{seat}` | 시설 관리 팬틸트 각도 등록 |
| ↑ | `clip_request` | `{"event_id":17}` | 이벤트 창 '녹화 영상 다시 보기' |
| ↓ | `clip_info` | `{event_id, status: "none"\|"recording"\|"saved"\|"deleted", started_at, ended_at, path}` | `clip_request` 응답. Console은 같은 노트북이라 `service/jarvis_DB/` + `path`를 직접 연다 |
| ↑ | `ask` | `{"ask_id","text"}` (1~500자) | [질의] 강사 자연어 질문. vision 이 없으면 control 이 바로 `ask_answer mode=error` |
| ↓ | `ask_answer` | 위 vision 표와 같음 | [질의] 답 + 근거 도구 목록 |
| ↑ | `report_request` | `{"report_id"}` | [리포트] 강사 [수업 종료 리포트] (오늘 하루) |
| ↓ | `report` | `{"report_id","status":"writing"\|"done"\|"error","data","summary","suggestions","mode","took_s","fallback"}` | [리포트] 숫자 먼저(writing) → 문장 붙여 한 번 더(done) |

| ↑ | `view_request` | `{"seat": 1~4 \| null}` | [수동보기] 강사 화면 [원위치] (null) 등 |
| ↓ | `view_state` | `{"seat","until","by"}` / `{"seat":null,"why":"timeout"\|"event"\|"request"}` | [수동보기] 시작·끝 → Console 이 카메라 보기 창을 열고 닫음 |

[수동보기] 강사 "1번 좌석 보여줘" (10/22 결정):

- AI 조교가 `tool_call look_at_seat {seat}` / `camera_home {}` 를 보내거나 Console 이 `view_request` 를 보내면
  hub `camera_tool()` 이 각도 미등록만 거르고 `EventManager.on_view_request(seat, now, by)` 에 맡긴다. **판단은 event_manager.**
- **열린 이벤트를 추적 중이면 거절** (`ok:false, reason:"tracking"`). 이벤트가 없을 때만 `pantilt preset {kind:0}`(LED 끔) + `track_target`.
- `view_hold_s`(control.yaml timers, 60초) 뒤 tick 이 원위치(home). 그 전에 새 이벤트가 오면 `_reschedule()` 이 바로 그 이벤트로 넘긴다.
- 이벤트가 아니므로 help_event·event_log·녹화에 남기지 않는다. 확인 단계 없이 바로 실행 (되돌리기 쉬움).

[리포트] 수업 종료 리포트 (기능 3, 10/22):

- 강사가 누를 때만 만든다. 범위는 오늘 하루(0시~지금). DB 에 리포트를 저장할지는 미정 (지금은 화면·PDF 만).
- 숫자: hub 가 `AskTools.report_data()` 로 바로 집계해 Console 에 `report status=writing` 으로 먼저 보낸다.
  문장: vision 에 `report_write {report_id, data, now}` → `report_text {summary, suggestions, mode, took_s, fallback}` 가 오면
  `report status=done` 으로 한 번 더 보낸다. vision 이 없으면 숫자만 `done`(mode `none`).
- `report_data`: 종류별 건수, 평균·최대 확인 시간, 평균 처리 시간, 결과별 건수, 오감지 목록, 쓰러짐 단계 알림, 좌석별 건수(키는 문자열),
  시간대별 반 평균(class_condition), 장비 끊김 구간(device_log). **좌석 번호만, 반 평균만** (학생 이름·개인 값 없음).
- 메시지 코드는 새 칸 0x6_ (`report_request` 0x60, `report` 0x61, `report_write` 0x62, `report_text` 0x63). console 0x4_ 가 다 찼다.

[질의] 강사 자연어 질의 (기능 2, 10/22, 1차는 조회만):

- LLM 은 vision(`ask.py`)에 있고, 데이터는 control 만 본다. LLM 이 고른 도구를 vision 이 `tool_call` 로 보내면
  hub 가 `hub/ask_tools.py`(`AskTools.run`)로 실행해 `tool_result` 로 돌려준다. 판단 아님 → `event_manager` 를 거치지 않는다.
- `ask_tools.py` 는 **읽기 전용**(SELECT 와 `EventManager.snapshot()`)이다. LLM 이 SQL 을 쓰지 않는다. 인자는 여기서 다시 검사.
- **학생 이름을 내보내지 않는다** (좌석 번호로만 답하기로 결정). `seat_registration` 을 읽지 않는다.
- 도구: `get_open_events`, `search_events(kind, seat, since, until, status, limit≤50)`, `get_event_timeline(event_id)`,
  `get_stats(since, until)`, `get_device_status`, `get_class_condition(minutes)`. 시각은 control 지역 시간 `"YYYY-MM-DD HH:MM"`, `"today"`.
  도구 설명은 vision `ask.py` 의 `TOOL_DECLS` 와 맞춘다.
- 조치(확인·완료·오감지)는 2차 — "실행할까요?" 확인 단계를 붙여서.
- 작은 모델 실수 받아 주기 (10/22): `until="today"` = 오늘 끝, `"yesterday"`/`"now"` 허용, 미래 `since` 는 고치라는 오류,
  0건이면 `note`, 장비 상태는 `summary`·`"3분 전"`. 이름·조치 질문은 vision 이 LLM 없이 규칙으로 답한다(mode `rule`).
- `ask_answer.fallback`: Gemini 등을 못 쓴 이유 목록 (예: `HTTP 429`). Console 에 보여 줄 수 있다.

화면은 `event_update` 최신 값으로 덮어쓰기만 한다. 화면에서 상태를 계산하지 않는다.

control ↔ student_client_N:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↓ | `fall_alert` | `{"target_seat":3,"stage":"neighbor"\|"all"}` | 미확인 15초 → 옆자리, 30초 → 전체 |
| ↓ | `fall_alert_cancel` | `{"target_seat":3}` | 누군가 확인했을 때 |
| ↑ | `fall_confirm` | `{"target_seat":3}` | 학생 확인 버튼 |
| ↑ | `condition_summary` | `{"focus_low":false,"posture_bad":true}` | 10초마다 |
| ↑ | `seat_register` | `{"student_name","baseline","zones"}` | 학생 [자리 등록]. hub가 `db.register_seat` → 강사 화면에 `seat_info` |

공통: `heartbeat`(2초마다, 6초 없으면 offline, 기록 안 함), `node_info`(접속 시 한 번), `rx_ack`(사건 수신 확인 — 강사 confirm과 다름).

## event_manager.py 동작

상태는 `active`(추적 중) / `waiting`(대기열) / `closed`(종료) 세 가지뿐.
`confirm`은 상태가 아니다. `confirmed_at`을 기록하고 단계 알림만 멈춘다.
정렬 기준은 `(priority, detected_at)`.

입력별 처리:

| 입력 | 처리 |
| --- | --- |
| 사건 `start` | 같은 좌석·같은 종류의 열린 이벤트가 있으면 무시(손 내림·일어남 뒤 다시 start 면 `explain_request again`). 없으면 help_event 생성 → 대기열 → `reschedule()` → `explain_request detect` |
| `question` `end` | 닫지 않는다. `hand_down` 표시만 (자동 제거 여부는 논의 중) |
| `away` `end` | `closed`, `close_reason=cleared`, 복귀 시각 기록 |
| `fall` `end` | 자동으로 닫지 않는다. 강사 화면에 "일어남"만 표시 |
| `confirm` / `fall_confirm` | `confirmed_at`, `confirmed_by` 기록. 쓰러짐이면 `fall_alert_cancel` |
| `resolve` | `closed`, `close_reason=resolved` → `reschedule()` |
| `dismiss` | `closed`, `close_reason=false_alarm` → `reschedule()` (그 외는 `resolve`와 같음) |
| `track_offset` | 지금 active 좌석의 결과일 때만 `pan_tilt nudge`로 변환 |
| 노드 offline | `device_update`. vision_service면 "감지 중단" 표시. 팬틸트(Uno) 통신 오류는 `pan_tilt_state` SAFE → "추적 중단 (알림은 계속)" |

녹화 (`to="recorder"`, hub가 `db.start_clip`/`end_clip` + `ClipRecorder`로 실행):

| 때 | 할 일 |
| --- | --- |
| 사건 `start`로 이벤트 생성 (확정) | `recorder start {event_id, seat, kind}` → event_clip 행(status 1, path `clips/{event_id}.mp4`), event_log `clip` |
| `_close` (resolve·dismiss·expire·cleared) | `recorder stop {event_id}` → 파일을 닫으면 status 2, `keep_until = ended_at + clip_keep_days` |
| 재시작 `start()` | 열린 이벤트마다 `recorder start` (새 파일 `clips/{event_id}_2.mp4`). 끊긴 녹화 행은 hub가 시작할 때 status 2로 닫음 |
| 10분마다 (hub) | `keep_until`이 지난 녹화 파일 삭제 → status 3 |

tick (0.1초마다, Control 시계):

1. 쓰러짐 단계 알림: 미확인 15초 → 옆자리(`seat.neighbors`) `fall_alert neighbor`, 30초 → 전체 `fall_alert all`
2. 질문 만료: `question_expire_s` 초과 → `closed`(expired)
3. 자리비움 미복귀: `away_alert_s` 초과 → 강사에게 재알림
3-1. [수동보기] `view_hold_s` 가 지나면 수동 보기 끝 → 원위치
4. `reschedule()`: 대기열 맨 앞이 지금 active와 다르면 교체(선점 포함) → `track_target` + `pan_tilt preset`. 비면 `home`

[알림문구] `explain_request.situation` (강사용 알림 문구를 다시 만드는 순간, `_explain()`):

| situation | 언제 | 비고 |
| --- | --- | --- |
| `detect` | 새 이벤트 생성 | 선점했으면 `preempted_kind/seat`, 대기면 `queue_pos` |
| `again` | 손 내림·일어남 뒤 다시 start | |
| `fall_neighbor` / `fall_all` | 쓰러짐 미확인 15초 / 30초 (`fall_alert` 보낼 때) | |
| `fall_risen` | `fall` `end` (확인 전일 때만) | |
| `hand_down` | `question` `end` | |
| `away_overdue` | 자리비움 `away_alert_s` 초과 | |

- 요청마다 이벤트의 `explain_seq`가 1씩 오르고, `explain_text`의 `seq`가 그것과 다르면(더 새 요청이 나간 뒤 늦게 온 응답) 버린다.
- `explain_seq`, `explain_situation`은 화면 표시용이라 `event_update`에만 싣고 DB에는 없다. DB `help_event.explain_text`는 마지막 문구로 덮어쓴다.
- 문구 생성은 vision_service(`llm.py`)가 한다. 터미널 시험: `service/jarvis_vision_service/alert_demo.py`.

설정 (`config/control.yaml`, 좌석 각도는 예시값 — 박병준님 측정값으로 교체):

```yaml
seats:
  1: {pan: 60,  tilt: 85, neighbors: [2, 3]}   # 2×2 (1 2 / 3 4), 가로·세로 옆자리
  2: {pan: 80,  tilt: 85, neighbors: [1, 4]}
  3: {pan: 100, tilt: 85, neighbors: [1, 4]}
  4: {pan: 120, tilt: 85, neighbors: [2, 3]}
priority: {fall: 1, question: 2, away: 3}
timers:
  fall_neighbor_s: 15
  fall_all_s: 30
  question_expire_s: 180    # 임시값
  away_alert_s: 60          # 임시값
track:
  nudge_max_hz: 10
  nudge_gain: 8.0           # 측정 후 조정
clips:                      # 이벤트 녹화 (Data Structure v2 4장)
  dir: service/jarvis_DB/clips   # 저장소 최상위 기준
  fps: 10
  keep_days: 7              # 임시 (app_setting.clip_keep_days 가 있으면 우선)
```

실행 옵션: `--no-clips` 녹화만 끄기, `--no-video` 영상 중계를 끄면 녹화도 꺼진다.

## DB (`service/jarvis_DB/create.sql`)

원본: Confluence "Data Structure" v2 (2026-10-21) 2장. 스키마는 create.sql 이 기준이고 `SCHEMA_VERSION = 3`.

| 테이블 | 내용 |
| --- | --- |
| `seat` | 좌석 1~4: label, neighbors(2×2), device_node(11~14), has_preset, reg_id(지금 앉은 학생) |
| `seat_registration` | 자리 등록 이력: 이름, baseline(JSON 7개), zones, registered_at, replaced_at(NULL=현재) |
| `device` / `device_log` | 장비(0 control · 1 vision · 2 팬틸트(가상) · 3 console · 11~14 학생 · 21 cam1 · 22 cam2) / 연결 이력 |
| `help_event` | 도움 이벤트 (숫자 코드), reg_id = 그때 앉아 있던 학생, hand_down 저장 |
| `event_log` | 이벤트 처리 기록, actor = 장비 번호 (0 system) |
| `event_clip` | 이벤트 녹화 (cam2), status 1 녹화 중 · 2 저장됨 · 3 삭제됨 |
| `class_condition` | 반 평균 (10초마다) |
| `app_setting` | 강사 화면 [설정] (JSON 값) |

- 상태값은 Interface Spec 숫자 코드: kind 1 fall · 2 question · 3 away / status 1 active · 2 waiting · 3 closed /
  alert_stage 0 · 1 neighbor · 2 all · 3 long / close_reason 0 · 1 resolved · 2 false_alarm · 3 expired · 4 cleared /
  explain_mode 0 · 1 gemini · 2 ollama · 3 default.
- `event_manager`는 계속 문자열로 다룬다. 문자열 ↔ 숫자, 노드 이름 ↔ 장비 번호 변환은 `db.py`에서만 한다.
- `messages`, `nodes`, `node_status` 폐지. heartbeat·track_offset·원본 메시지는 기록하지 않는다.
  heartbeat는 `device.last_seen`(학생은 `rtt_ms`·`cam_ok`, vision은 `cam1_fps`/`cam2_fps` → 장비 21·22)만 갱신.
- `device_update`, `class_condition`을 보낼 때 hub가 그 내용을 `device`·`device_log`, `class_condition`에 기록한다.
- 외래 키(PRAGMA foreign_keys)는 켜지 않는다 (seat ↔ device, seat ↔ seat_registration 순환 참조).
- 스키마를 바꾸면 개발 중에는 `db/hub.db`를 지우고 다시 만든다. 마이그레이션 안 함.

## 구현 규칙

- 노드 이름, 메시지 종류, 좌석은 `common/protocol.py`에 먼저 추가한다.
- 판단은 `event_manager.py` 밖에서 하지 않는다.
- DB 쓰기는 `db.py`에서만.
- asyncio 루프를 막지 않는다. sqlite 쓰기는 지금 규모에서 허용, 느려지면 executor로.
- `event_manager`는 시계를 인자로 받는다(`tick(now)`). 가짜 시계로 테스트할 수 있어야 한다.

## 테스트

| 단계 | 방법 | 확인 |
| --- | --- | --- |
| 단위 | `event_manager`에 가짜 시계 + 사건 순서대로 | 선점, FIFO, 15/30초 알림, 만료, 중복 무시 |
| 단위 | `tools/test_protocol.py` | 바이너리 프레임 왕복, 헤더 배치, 쪼개짐·붙음, CRC, 옛 형식 거부 |
| 통신 | `tools/fake_node.py`로 vision_service·instructor_console 흉내 | question start → event_update → confirm → resolve → queue_state |
| 통합 1 (10/20) | 실제 Vision B → Control → 대시보드 → 팬틸트 preset | 질문 1건이 끝까지 흐르는지 |
| 통합 2 | 질문 추적 중 쓰러짐 | 선점, 학생 노트북 fall_alert |
| 통신 (영상) | streamer → control → vision_service | Vision이 받는 cam1·cam2 fps가 직접 받을 때와 비슷한지, control `tick`이 밀리지 않는지 |
| 장애 | vision_service 끄기, Arduino 뽑기 | "감지 중단" 표시, 추적만 멈추고 알림 계속 |
| 장애 | camera_streamer 끄기 | 해당 카메라 fps 0 → 장비 상태에 표시 |

## 아직 정해지지 않은 것 (임의로 정하지 말고 물어볼 것)

1. `question end` 시 대기열에서 자동으로 뺄지
2. 자리비움도 팬틸트로 추적할지
3. `question_expire_s`, `away_alert_s` 값
4. 최상위 `common/` 폴더 추가 (강사님 확인)
5. 대시보드에 추적 카메라 영상을 띄울지 — SA 그림의 Control ↔ Instructor Console이 TCP/UDP로 되어 있음. UDP가 영상 송출용인지 확인
6. Interface Specification(IF-01~05) 수정 담당·시점 (영상 경로 변경도 반영 필요)
7. 카메라 상태(fps)를 Control이 직접 잴지, 지금처럼 `vision_service` 하트비트로 받을지
8. 영상 중계 상세: 받는 포트·보내는 포트, 패킷 형식, 그대로 넘길지 재인코딩할지, 중계 코드 위치(파일)
