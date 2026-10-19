# Control Server (Jarvis Control Service)

이 폴더에서 작업할 때 따르는 설계 기준.
원본: Confluence "Control Server 상세 설계" v0.1 (2026-10-19) — https://skypol-sesac.atlassian.net/wiki/x/BABt
원본이 바뀌면 이 파일도 다시 맞춘다. 원본과 다르면 **원본이 우선**이다.

## 핵심 원칙

- **기존 코드(지우님 구현)를 기반으로 하고, 비어 있는 이벤트 관리만 추가한다. 새로 짜지 않는다.**
- 모든 프로그램(Vision, 팬틸트 제어, 강사 화면, 학생 화면)은 TCP 노드로 Control에 붙고, 통신은 `NodeClient` 하나를 같이 쓴다.
- 카메라 영상은 Control을 거치지 않는다. UDP로 Vision Service에 바로 간다.
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
│   ├─ hub/control_config.py                  control 전용: control.yaml 읽기, DB·스키마 경로
│   ├─ config/control.yaml                    좌석 각도·옆자리·타이머 (control 만)
│   ├─ db/hub.db                              SQLite 파일
│   ├─ tests/, tools/
│   └─ run_hub.py, run_client.py
├─ service/jarvis_DB/create.sql               DB 스키마 원본 (db.py 가 시작할 때 실행)
├─ service/jarvis_vision_service/run_assistant.py   vision_service 노드
├─ hw/camera_controller/run_controller.py     camera_controller 노드 (Serial → Arduino)
└─ hw/camera_streamer/run_streamer.py         UDP 영상 송출 (TCP 노드 아님)
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
| `tests/test_event_manager.py` | (위치 미정) |

## 실행 / 시험

```bash
python3 run_hub.py                                  # control 서버 (또는 python3 -m hub -v)
python3 -m unittest discover -s tests -v            # event_manager 단위 테스트 (가짜 시계)
python3 -m tools.fake_node --server 127.0.0.1       # 통신 시나리오: 질문 → 쓰러짐 선점 → confirm/resolve
python3 -m tools.fake_node --server 127.0.0.1 --fall-wait 31   # 15초/30초 fall_alert 까지
```

- `SchemaMismatch` 오류가 나면 `db/hub.db`, `db/hub.db-wal`, `db/hub.db-shm`을 지우고 다시 실행한다. 스키마를 바꾸면 `db.py`의 `SCHEMA_VERSION`을 올린다.

## 코드 구조 (판단과 실행의 분리)

- `event_manager.EventManager`는 네트워크도 DB도 모른다. 입력(`on_incident`, `on_confirm`, `on_resolve`, `tick(now)` …)을 받으면 할 일 `Effects(sends, saves, logs)`를 돌려준다.
- `hub.HubServer.apply()`가 그것을 `db.apply()`로 기록하고 `send()`로 보낸다. hub에는 판단을 넣지 않는다.
- 재시작하면 `help_event`에서 열린 이벤트를 읽어 `EventManager`를 복구한다.

## 설계 문서에 없어서 구현에서 정한 것 (바꿔도 됨, 팀에 공유할 것)

- 질문 만료는 **강사가 confirm하지 않은 질문만** 해당된다.
- 자리비움 미복귀 재알림은 한 번만 하고, `event_update`의 `overdue=true`로 알린다.
- `hand_down`(손 내림), `risen`(일어남), `overdue`는 화면 표시용이라 `event_update`에만 싣고 DB에는 없다.
- 쓰러짐이 `end`(일어남)여도 confirm 전까지 단계 알림은 계속된다.
- `fall_alert_cancel`은 그 단계에서 알림을 받은 학생에게만 보낸다(neighbor면 옆자리, all이면 전체).
- `track_offset`은 메시지의 `seat`이 지금 active 좌석과 같아야 처리한다. Vision은 `seat`을 꼭 채운다.
- nudge 각도 = `dx·nudge_gain`, `dy·nudge_gain`. 방향이 반대면 `nudge_gain`을 음수로 한다.
- `class_condition`은 최근 30초 안에 보고한 학생만으로 비율을 내고, `reporting`(보고한 수)도 같이 보낸다.
- 다시 접속한 `vision_service`·`camera_controller`에는 지금 추적 대상(`track_target`, `pan_tilt preset/home`)을 다시 보낸다.
- `NodeClient`는 끊긴 동안 사건 외에 `confirm`·`resolve`·`fall_confirm`도 모아 두었다가 보낸다.

## 10/19 결정 사항

- D1 고정 카메라는 영상 송출만. 두 카메라 모두 `vision_service`(Vision B)가 판정.
- D2 Vision A는 옵션.
- D3 LLM: Gemini 기본, Ollama 백업.
- D4 Vision은 TCP 노드로 사건을 보낸다 (SA와 다름, 아래 참고).
- D6 강사의 확인 = `confirm`, 완료 = `resolve`. 수신 확인은 `ack` → `rx_ack`.
- D7 학습 자세는 좌석별 사건이 아니라 반 집계(`condition_summary`)로만.
- D8 좌석은 1~4번. 5번(매트) 없음.
- D9 하트비트는 `messages`에 기록하지 않음. 타이머는 Control 시계 기준. asyncio 메인 루프가 0.1초마다 `tick` 호출.

## SA와 다른 점 (SA 그림은 유지)

- DEV-01 Control ↔ Vision: HTTP 대신 TCP(`NodeClient`).
- DEV-02 Camera Streamer → Vision Service로 UDP 직접 (Control 경유 안 함).
- DEV-03 Control → `camera_controller` 노드(TCP) → Serial → Arduino. 시리얼 포트는 그 노드만 잡는다.
- DEV-04 DB는 SQLite 파일, Control만 접근.

## 이름 규칙 (소문자_밑줄)

노드 (`protocol.VALID_NODES`):

| 노드 | 실행 파일 | 비고 |
| --- | --- | --- |
| `control` | run_hub.py | hub가 보내는 메시지의 node 값 |
| `vision_service` | run_assistant.py | 기존 fixedcam/tracker 대체 |
| `camera_controller` | run_controller.py | 신규 |
| `instructor_console` | run_console.py | 신규, 메인 대시보드 |
| `student_client_1` ~ `student_client_4` | run_client.py | 기존 student1~4 |

카메라 스트리머는 TCP 노드가 아니다. 카메라 상태는 `vision_service` 하트비트에 `cam1_fps`, `cam2_fps`로 실린다.

사건 종류:

| 기존 | 변경 | 우선순위 |
| --- | --- | --- |
| `fall` | `fall` | 1 |
| `hand_raise` | `question` | 2 |
| `leave_seat` | `away` | 3 |
| `drowsy`, `posture` | 삭제 → `condition_summary`(집계) | - |

공통 메시지 형식 (기존 형식 유지):

```json
{"v":1, "node":"vision_service", "seq":1042, "type":"question", "seat":2,
 "ts":1760500000.123, "value":{"state":"start"}, "conf":0.81}
```

- `ts`는 보낸 PC의 시계. Control은 받는 순간 `rx_ts`(Control 시계)를 붙이고 **모든 판단은 `rx_ts`로 한다.**
- Interface Specification(IF-01~05)과 다르면 코드가 기준이다.

## 메시지 목록

vision_service ↔ control:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↑ | `fall`/`question`/`away` | `{"state":"start"\|"end"}` | 판정 확정/해제 순간 한 번 (유지 시간은 Vision이 지킴) |
| ↑ | `track_offset` | `{"found":true,"dx":0.12,"dy":-0.05}` | 추적 중 10~15회/초. `messages`에 기록 안 함 |
| ↑ | `explain_text` | `{"event_id","text","mode":"gemini"\|"ollama"\|"default"}` | 설명 요청 응답 |
| ↑ | `heartbeat` | `{"fps","cpu","cam1_fps","cam2_fps"}` | 2초마다 |
| ↓ | `track_target` | `{"seat":2}` 또는 `{"seat":null}` | 추적 대상 변경 시 |
| ↓ | `explain_request` | `{"event_id","kind","seat","elapsed_s","waiting_count"}` | 새 이벤트 발생 시 (비동기) |

control ↔ camera_controller:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↓ | `pan_tilt` | `{"mode":"preset","seat":2}` | 추적 대상 변경 시. 각도는 control.yaml |
| ↓ | `pan_tilt` | `{"mode":"nudge","dpan":1.5,"dtilt":-0.8}` | `track_offset` 수신 시 (최대 10Hz) |
| ↓ | `pan_tilt` | `{"mode":"home"}` | 대상 없을 때 |
| ↑ | `pan_tilt_state` | `{"pan":95,"tilt":80,"ok":true}` | 명령 적용 후 (Serial `OK` 전달) |

control ↔ instructor_console:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↓ | `event_update` | help_event 한 행 전체 | 이벤트 생성/변경마다 |
| ↓ | `queue_state` | `{"active":17,"waiting":[18,15]}` | 대기열 변경 시 |
| ↓ | `device_update` | `{"node","online","note"}` | 장비 상태 변경 시 |
| ↓ | `class_condition` | `{"focus_low_ratio","posture_bad_ratio"}` | 10초마다 (반 평균만) |
| ↓ | `snapshot` | 열린 이벤트 전체 + 장비 상태 | Console 접속/재접속 시 |
| ↑ | `confirm` | `{"event_id":17}` | 강사 "확인" → 단계 알림 멈춤 |
| ↑ | `resolve` | `{"event_id":17}` | 강사 "완료" → 이벤트 종료, 다음 대상 |

화면은 `event_update` 최신 값으로 덮어쓰기만 한다. 화면에서 상태를 계산하지 않는다.

control ↔ student_client_N:

| 방향 | type | value | 때 |
| --- | --- | --- | --- |
| ↓ | `fall_alert` | `{"target_seat":3,"stage":"neighbor"\|"all"}` | 미확인 15초 → 옆자리, 30초 → 전체 |
| ↓ | `fall_alert_cancel` | `{"target_seat":3}` | 누군가 확인했을 때 |
| ↑ | `fall_confirm` | `{"target_seat":3}` | 학생 확인 버튼 |
| ↑ | `condition_summary` | `{"focus_low":false,"posture_bad":true}` | 10초마다 |

공통: `heartbeat`(2초마다, 6초 없으면 offline, 기록 안 함), `node_info`(접속 시 한 번), `rx_ack`(사건 수신 확인 — 강사 confirm과 다름).

## event_manager.py 동작

상태는 `active`(추적 중) / `waiting`(대기열) / `closed`(종료) 세 가지뿐.
`confirm`은 상태가 아니다. `confirmed_at`을 기록하고 단계 알림만 멈춘다.
정렬 기준은 `(priority, detected_at)`.

입력별 처리:

| 입력 | 처리 |
| --- | --- |
| 사건 `start` | 같은 좌석·같은 종류의 열린 이벤트가 있으면 무시. 없으면 help_event 생성 → 대기열 → `reschedule()` → `explain_request` |
| `question` `end` | 닫지 않는다. `hand_down` 표시만 (자동 제거 여부는 논의 중) |
| `away` `end` | `closed`, `close_reason=cleared`, 복귀 시각 기록 |
| `fall` `end` | 자동으로 닫지 않는다. 강사 화면에 "일어남"만 표시 |
| `confirm` / `fall_confirm` | `confirmed_at`, `confirmed_by` 기록. 쓰러짐이면 `fall_alert_cancel` |
| `resolve` | `closed`, `close_reason=resolved` → `reschedule()` |
| `track_offset` | 지금 active 좌석의 결과일 때만 `pan_tilt nudge`로 변환 |
| 노드 offline | `device_update`. vision_service면 "감지 중단" 표시, camera_controller면 추적만 멈추고 알림은 계속 |

tick (0.1초마다, Control 시계):

1. 쓰러짐 단계 알림: 미확인 15초 → 옆자리(`seat.neighbors`) `fall_alert neighbor`, 30초 → 전체 `fall_alert all`
2. 질문 만료: `question_expire_s` 초과 → `closed`(expired)
3. 자리비움 미복귀: `away_alert_s` 초과 → 강사에게 재알림
4. `reschedule()`: 대기열 맨 앞이 지금 active와 다르면 교체(선점 포함) → `track_target` + `pan_tilt preset`. 비면 `home`

설정 (`config/control.yaml`, 좌석 각도는 예시값 — 박병준님 측정값으로 교체):

```yaml
seats:
  1: {pan: 60,  tilt: 85, neighbors: [2]}
  2: {pan: 80,  tilt: 85, neighbors: [1, 3]}
  3: {pan: 100, tilt: 85, neighbors: [2, 4]}
  4: {pan: 120, tilt: 85, neighbors: [3]}
priority: {fall: 1, question: 2, away: 3}
timers:
  fall_neighbor_s: 15
  fall_all_s: 30
  question_expire_s: 180    # 임시값
  away_alert_s: 60          # 임시값
track:
  nudge_max_hz: 10
  nudge_gain: 8.0           # 측정 후 조정
```

## DB (`service/jarvis_DB/create.sql`)

```sql
CREATE TABLE IF NOT EXISTS seat (
  seat       INTEGER PRIMARY KEY,         -- 1~4
  label      TEXT,
  pan        REAL, tilt REAL,
  neighbors  TEXT                         -- "2,4"
);

CREATE TABLE IF NOT EXISTS help_event (
  event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  seat          INTEGER NOT NULL REFERENCES seat(seat),
  kind          TEXT NOT NULL,            -- fall | question | away
  priority      INTEGER NOT NULL,
  status        TEXT NOT NULL,            -- active | waiting | closed
  detected_at   REAL NOT NULL,            -- Control 시계
  confirmed_at  REAL, confirmed_by TEXT,
  closed_at     REAL, close_reason TEXT,  -- resolved | expired | cleared
  alert_stage   TEXT DEFAULT 'none',      -- none | neighbor | all
  explain_text  TEXT, explain_mode TEXT,
  source        TEXT                      -- "vision_service:1042"
);

CREATE TABLE IF NOT EXISTS event_log (
  log_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id  INTEGER NOT NULL REFERENCES help_event(event_id),
  ts        REAL NOT NULL,
  action    TEXT NOT NULL,  -- detect | activate | preempt | wait | confirm | alert | resolve | expire | clear
  actor     TEXT NOT NULL,  -- system | instructor_console | student_client_N
  detail    TEXT            -- JSON
);
```

기존 테이블:
- `messages` 유지. 하트비트와 `track_offset`은 기록 안 함. `ts`와 `rx_ts` 둘 다 저장.
- `nodes` 유지.
- `node_status` 유지 + `online`, `cam1_fps`, `cam2_fps` 추가.
- `cameras`, `seats`(노드 보고 방식) 폐지. 좌석은 yaml → `seat` 테이블.

- 이름과 영상은 저장하지 않는다. 학생은 좌석 번호로만 구분.
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
| 통신 | `tools/fake_node.py`로 vision_service·instructor_console 흉내 | question start → event_update → confirm → resolve → queue_state |
| 통합 1 (10/20) | 실제 Vision B → Control → 대시보드 → 팬틸트 preset | 질문 1건이 끝까지 흐르는지 |
| 통합 2 | 질문 추적 중 쓰러짐 | 선점, 학생 노트북 fall_alert |
| 장애 | vision_service 끄기, Arduino 뽑기 | "감지 중단" 표시, 추적만 멈추고 알림 계속 |

## 아직 정해지지 않은 것 (임의로 정하지 말고 물어볼 것)

1. `question end` 시 대기열에서 자동으로 뺄지
2. 자리비움도 팬틸트로 추적할지
3. `question_expire_s`, `away_alert_s` 값
4. 최상위 `common/` 폴더 추가 (강사님 확인)
5. 대시보드에 추적 카메라 영상을 띄울지
6. Interface Specification(IF-01~05) 수정 담당·시점
