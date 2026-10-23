# jarvis_vision_service (vision_service 노드)

고정카메라·능동카메라 영상을 분석하고, 중앙 서버(Control)에 좌석별 관측과 추적 좌표를 보내는 Vision 노드다. AI 조교의 상황 설명·자연어 질의·수업 종료 리포트도 같은 프로세스에서 처리한다.

현재 카메라 운영 범위는 **손들기(질문)·자리비움·능동카메라 추적**이다. 쓰러짐 감지는 포함하지 않는다. 아래 AI 조교 문서의 쓰러짐 관련 메시지·예제는 기존 프로토콜과 시험 기록이며, 현재 카메라가 쓰러짐을 검출한다는 뜻은 아니다.

## 카메라 인식·추적 흐름

1. 카메라가 연결된 PC에서 `run_fixed_camera.py`(cam1), `run_pantilt_camera.py`(cam2)를 실행해 영상을 Control에 보낸다.
2. Control은 영상을 강사 화면과 Vision에 중계한다. Vision은 웹캠을 직접 열지 않는다.
3. Vision은 cam1을 YOLO Pose·ByteTrack으로 분석해 좌석별 재실·손들기 관측(`seat_obs`)을 전송한다.
4. Control이 관측 유지시간으로 이벤트를 확정하고, 우선순위·대기열에 따라 대상 좌석을 선택한다. PanTiltService가 좌석 프리셋으로 이동하고 Vision에는 `track_target`을 보낸다.
5. Vision은 cam2에서 검출한 코 위치를 `track_obs`로 보낸다. 실제 각도 보정·재탐색·원위치 복귀와 Arduino 시리얼 통신은 Control 안의 PanTiltService가 담당한다.

| 메시지 | 방향 | 내용 |
| --- | --- | --- |
| `seat_obs` | Vision → Control | 세션·프레임·시각, 좌석별 `present`, `hand_up`, 판정 근거 |
| `track_target` | Control → Vision | 대상 `seat`, 대상 변경 세대 번호 `gen` |
| `track_obs` | Vision → Control | 좌석, `gen`, 프레임·촬영 시각, `TRACKED` 또는 `LOST`, 유효하면 `cx`, `cy` |

`cx`, `cy`는 **cam2 영상 기준 0~1 좌표**이며 화면 중앙은 `(0.5, 0.5)`다. cam1의 사람 ID나 좌표를 cam2에 그대로 사용하지 않는다. `gen`은 이전 대상의 늦은 관측을 구분하는 데 사용한다. 노드 메시지는 공통 TCP v3 바이너리 프로토콜을 사용한다.

### 고정카메라 좌석 관측

최초 등록 때 좌석 번호를 선택하고 앉아 있는 사람의 어깨 중심 근처를 클릭해 기준 위치·크기를 저장한다. 좌석별 영상을 잘라내거나 사각형을 그려 겹침률로 판단하는 방식이 아니다.

- 고정카메라는 높이를 확보해 앞사람이 뒷사람의 상체·팔을 가리는 정도를 줄인다. 위치·각도 변경 후에는 좌석을 다시 등록한다.
- `present`와 `hand_up`은 각각 `True` / `False` / `None`이다. `None`은 관측 불가이며 부재나 손 내림으로 취급하지 않는다.
- 몸 숙임·가림·좌석 연결이 애매한 상황은 보수적으로 UNKNOWN이 될 수 있다. 카메라 밖으로 나간 경우도 등록 위치 주변의 관측과 중앙 유지시간 판단을 거친다.
- 영상 비율이 등록 당시와 다르면 `ASPECT_CHANGED`, 분석 결과가 오래됐으면 `STALE`로 보낸다.
- 실제 이벤트 확정은 Control에서 한다. 단독 실행기의 `DecisionDemo`는 로컬 시험용이다.

### 능동카메라 추적 조건

- 화면에서 **사람 한 명이 검출되고 코 키포인트가 유효할 때** 좌표를 보낸다.
- 사람이 없거나 여러 명이 검출되거나 코가 유효하지 않으면 `LOST`를 보낸다. 두 카메라 사이 동일 인물 식별은 구현돼 있지 않다.
- 대상 지정 이후 촬영된 프레임만 사용하며, 촬영부터 처리까지 0.3초를 넘으면 좌표 대신 `LOST`를 보낸다.
- 최근 프레임 하나를 우선 처리한다. 수신 시 카메라 PC와의 시계 차이를 추정·보정하고, 평소보다 0.8초 넘게 늦은 프레임은 버린다.

## 카메라 관련 파일·설정

경로는 저장소 루트 기준이다.

| 파일 | 역할 |
| --- | --- |
| `service/jarvis_vision_service/run_assistant.py` | 두 영상 분석·관측 전송·추적 대상 수신·AI 조교 통합 실행 |
| `service/jarvis_vision_service/run_fixed_vision.py` | 고정카메라 좌석 등록·단독 시험 |
| `service/jarvis_vision_service/fixed_camera/app.py` | 등록 화면·로컬 카메라 입력·시험 로그 |
| `service/jarvis_vision_service/fixed_camera/core.py` | 좌석 매칭·손들기 관측 |
| `service/jarvis_vision_service/fixed_camera/config.py` | 좌석 기준 저장·검증 |
| `service/jarvis_vision_service/fixed_camera/bytetrack_fixed.yaml` | 고정카메라 추적 설정 |
| `service/jarvis_vision_service/config/fixed_seats.local.json` | 현장 등록으로 생성하는 좌석 설정 |
| `hw/camera_streamer/run_fixed_camera.py` | 고정카메라 cam1 영상 송출 |
| `hw/camera_streamer/run_pantilt_camera.py` | 능동카메라 cam2 영상 송출 |
| `config/common.yaml` | Control IP·TCP 포트 |
| `service/jarvis_control_service/config/presets.json` | 현장에 맞게 등록한 팬틸트 좌석 각도 |

팬틸트 상세 동작은 [PanTiltService 문서](../jarvis_control_service/hub/pantilt/README.md), 배선·펌웨어는 [Camera Controller 문서](../../hw/camera_controller/README.md)를 참고한다.

## 카메라 실행 준비

### PC 배치·통신

아래 실행 예시는 **강사 컴퓨터에서 Control·Vision·강사 GUI를 함께 실행**하는 배치다. Vision 실행 위치가 별도 PC라면 그 PC로 좌석 설정·모델을 준비하고 `--server`에 강사 컴퓨터 IP를 넣는다.

| PC | 실행·연결 |
| --- | --- |
| 고정카메라 노트북 | 좌석 등록 후 cam1 송출 |
| 능동카메라 연결 PC | cam2 송출. 고정카메라 노트북과 같아도 됨 |
| 강사 컴퓨터 | Control·Vision·GUI 실행, Arduino USB 연결 |

현재 PanTiltService는 Control과 같은 프로세스에서 Arduino 시리얼 포트를 직접 연다. `run_controller.py`를 함께 실행하거나 시리얼 모니터를 열어 포트를 중복 점유하지 않는다.

| 통신 | 받는 PC·포트(기본값) |
| --- | --- |
| 노드 ↔ Control | 강사 컴퓨터 TCP 5000 (`config/common.yaml` 기준) |
| cam1 / cam2 → Control | 강사 컴퓨터 UDP 6001 / 6002 |
| Control → Vision | Vision PC UDP 5021 / 5022 |
| Control → GUI | GUI PC UDP 5011 / 5012 |

같은 LAN에서 해당 포트가 통신 가능해야 한다. 다른 PC에서 쓰는 `127.0.0.1`은 강사 컴퓨터가 아니라 그 PC 자신을 가리킨다. 카메라 송출의 `--target`에는 실제 강사 컴퓨터 LAN IP를 넣는다.

### Python·모델

각 터미널에서 해당 PC의 기존 프로젝트 가상환경을 활성화한다. 아래 명령의 `python3`가 그 환경을 사용하는지 확인한다.

```bash
# Vision·좌석 등록 PC
python3 -c "import cv2, numpy, ultralytics; print(cv2.__version__, ultralytics.__version__)"

# Control PC
python3 -c "import serial, yaml; print('control dependencies OK')"

# GUI PC
python3 -c "import PyQt6; print('GUI dependencies OK')"
```

카메라 송출 PC에는 OpenCV가 필요하다. Vision·좌석 등록에는 Ultralytics와 그 추론 환경이 추가로 필요하며, 현재 루트 `requirements.txt`에는 `ultralytics`가 명시돼 있지 않다. 기존 YOLO 실행 환경을 먼저 확인한다.

실제 보유한 `*-pose.pt` 경로를 지정한다. 아래 `yolo11s-pose.pt`는 예시이며 일반 객체 검출 모델은 사용할 수 없다. 통합 실행기는 모델을 자동 다운로드하지 않는다. CPU는 `--device cpu`, CUDA GPU는 `--device 0`을 사용한다.

## 카메라 실행방법

이 절의 명령은 **각 터미널에서 저장소 루트 `aiot_repo_3`로 이동한 상태**를 기준으로 한다. `/실제/경로/...`, `<강사컴퓨터_IP>`는 실제 값으로 바꾼다. 같은 프로그램은 한 번만 실행한다.

### 1. 고정카메라 노트북: 최초 좌석 등록

해당 카메라를 사용하는 송출기·다른 프로그램을 종료한 뒤 실행한다.

```bash
python3 service/jarvis_vision_service/run_fixed_vision.py \
  --source 0 --model /실제/경로/yolo11s-pose.pt \
  --device 0 --calibrate
```

1. 학생들이 평소 자세로 앉은 상태에서 화면에 키보드 포커스를 둔다.
2. `1`을 누르고 1번 학생의 양 어깨 중간 근처를 클릭한다. 약 2초간 자세를 유지해 저장 안내를 확인한다.
3. `2`, `3`, `4`도 같은 방식으로 등록한다. 등록된 좌석만 관측한다.
4. `Q` 또는 `Esc`로 종료한다. `--source`는 실제 장치 번호 또는 `/dev/videoN`에 맞춘다.

생성된 `service/jarvis_vision_service/config/fixed_seats.local.json`을 **강사 컴퓨터의 동일 경로로 복사**한다. Vision이 다른 PC에 있으면 그 PC로 복사한다. 이 로컬 파일과 모델은 Git pull만으로 준비된다고 가정하지 않는다. 카메라 높이·각도·구도를 바꾸면 재등록하고 새 파일을 다시 복사한다.

### 2. 강사 컴퓨터: Arduino·Control 시작

Arduino에 `hw/camera_controller/camera_controller.ino`를 업로드하고 시리얼 모니터를 닫는다. 실제 포트가 `/dev/ttyACM0`인지 확인한다.

```bash
python3 service/jarvis_control_service/run_hub.py \
  --host 0.0.0.0 --pantilt-port /dev/ttyACM0
```

Arduino 없이 통신만 점검할 때는 `--pantilt-port /dev/ttyACM0` 대신 `--pantilt-sim`을 사용한다. 모의 실행으로 실물 모터 동작을 확인할 수는 없다. 영상 분석 시험에서는 `--no-video`를 사용하지 않는다.

### 3. 강사 컴퓨터: GUI 시작

새 터미널에서 실행한다.

```bash
python3 gui/instructor_console/run_console.py --host 127.0.0.1
```

GUI가 별도 PC라면 `--host`를 강사 컴퓨터 IP로 바꾼다. 시설 관리에서 각 좌석을 비추도록 프리셋 각도를 확인·등록한다. 실제 좌석 각도를 예시 값으로 덮어쓰지 않는다. 영상 확인은 다음 송출기를 켠 뒤 진행한다.

### 4. 카메라 연결 PC: 두 영상 송출

고정카메라 노트북에서:

```bash
python3 hw/camera_streamer/run_fixed_camera.py \
  --device 0 --target <강사컴퓨터_IP>
```

능동카메라가 연결된 PC의 별도 터미널에서:

```bash
python3 hw/camera_streamer/run_pantilt_camera.py \
  --device 2 --target <강사컴퓨터_IP>
```

`0`, `2`는 예시 장치 번호다. USB 연결 순서에 따라 달라질 수 있으므로 장치 안내를 확인한다. 두 카메라가 같은 노트북에 연결돼 있어도 각각 별도 터미널에서 실행한다. 스트리머는 기본 15fps·가로 최대 640px로 JPEG를 송출한다.

### 5. 강사 컴퓨터: Vision 시작

좌석 설정 복사와 Pose 모델 준비를 확인하고 새 터미널에서 실행한다.

```bash
python3 service/jarvis_vision_service/run_assistant.py \
  --server 127.0.0.1 \
  --model /실제/경로/yolo11s-pose.pt \
  --device 0 --imgsz 640 \
  --seats service/jarvis_vision_service/config/fixed_seats.local.json
```

Vision은 별도 카메라 프로그램을 열지 않고 Control이 중계한 두 영상을 분석한다. 모델 두 개를 로드·예열하므로 초기 대기시간이 있다. 이 프로세스가 AI 조교도 함께 실행하므로 AI 조교용으로 같은 `run_assistant.py`를 하나 더 켜지 않는다. Gemini·Ollama 준비는 아래 AI 조교 설정을 따른다.

| 옵션 | 용도 |
| --- | --- |
| `--server`, `--port` | Control 주소·TCP 포트 재정의 |
| `--device cpu` | CPU 추론 시험 |
| `--imgsz 416` | 처리 속도 비교용 입력 크기 축소. 인식 결과도 다시 확인 |
| `--only-seat 1` | 등록된 1번 좌석만 시험 |
| `--record-keypoints /실제/경로/observations.jsonl` | cam1 좌표·좌석 관측 기록. 상위 폴더를 먼저 생성 |
| `--record-dir /실제/경로/recordings` | 선택적인 Vision 측 시험 녹화 |

Vision 측 녹화는 기본 꺼짐이며 분석한 프레임만 고정 15fps MP4와 시각 JSONL로 저장한다. Control의 이벤트 cam2 녹화·GUI 다시보기와는 별개다. 처리 속도가 낮으면 Vision MP4 재생 길이가 실제 촬영시간과 다를 수 있다.

### 6. 동작 확인·종료

1. 두 스트리머의 장치 열림·송출 FPS와 GUI의 cam1·cam2 영상을 확인한다.
2. Vision의 `cam1 analysis`, `cam2 analysis` 로그와 `좌석 판정` 요약을 확인한다.
3. 등록된 학생이 손을 들어 중앙의 질문 이벤트·대상 좌석 선택을 확인한다. Vision의 `Target` 로그 후 팬틸트가 해당 좌석 프리셋으로 이동하는지 본다.
4. cam2에 해당 학생 한 명이 잘 보이는 상태에서 코 위치에 따라 모터가 보정되는지 확인한다. 두 명이 검출되거나 코가 가려질 때는 `LOST` 경로로 처리돼야 한다.
5. 재실 확인 후 이석·복귀를 시험하고, 앞사람 가림·카메라 중단이 자리비움으로 오판되지 않는지 별도로 확인한다.
6. 질문 완료와 대기 이벤트 처리·대기 사건이 없을 때 원위치 복귀를 GUI와 모터에서 확인한다.

Vision 기본 로그가 `track_obs`를 매 프레임 출력하는 것은 아니다. 메시지 확인이 필요하면 Control을 `--verbose`로 실행한다. 영상 분석 FPS만으로 추적 성공을 판단하지 않는다. 종료는 각 터미널에서 `Ctrl+C`; Control 종료 시 팬틸트 정지·LED 끄기를 처리한다.

## 카메라 문제 확인

| 증상 | 확인·조치 |
| --- | --- |
| 카메라가 열리지 않음 | 장치 번호·USB 연결, 좌석 등록기 등 중복 점유 확인 |
| GUI 영상은 보이나 Vision 분석 로그가 없음 | Vision TCP 접속과 UDP 5021/5022 수신, 중복 Vision 프로세스 확인 |
| `ASPECT_CHANGED` | 등록·송출 영상 비율을 맞추고 필요하면 재등록 |
| UNKNOWN이 많음 | 앞뒤 사람 가림·양 어깨 검출·등록 기준 확인. 위치를 조정했다면 재등록 |
| `cam1 분석 ... 느리면 ...` | 2fps 미만 경고. GPU·모델 크기·입력 크기와 처리 지연 확인 |
| cam2 영상은 오지만 추적하지 않음 | 중앙 대상 지정, 프리셋, 사람 한 명·코 유효성, 300ms 이내 처리 여부 확인 |
| 프레임 버림·시계 차이 경고 | 네트워크·처리 부하와 PC 시간 동기화 확인. 시계 차이 보정 로그 자체가 연결 실패는 아님 |
| 팬틸트 SAFE·시리얼 오류 | Arduino 펌웨어·포트·권한·중복 점유 확인. 모터 상세 문서 참조 |

아래 AI 조교 절의 명령은 별도 안내가 없으면 `service/jarvis_vision_service` 폴더에서 실행한다.

---

## AI 조교 흐름

```
control ──explain_request {event_id, kind, seat, situation, seq, ...}──────▶ vision_service
                                                                                  │
                         ① Gemini  (gemini-3.5-flash-lite, 5초)                        │
                         ② 실패·타임아웃·잘린 답 → Ollama (qwen2.5:3b, 로컬)      │ llm.py
                         ③ 그것도 실패 → 기본 문구 "3번 좌석 쓰러짐 감지"          │
                                                                                  ▼
control ◀──explain_text {event_id, text, mode, seq, situation}─────────────── vision_service
   └─ help_event.explain_text / explain_mode 저장 → 강사 화면 event_update
```

- 어떤 경우에도 `explain_text` 는 꼭 간다 (최악이면 기본 문구).
- **상황별 알림 문구 (기능 1):** control 은 이벤트가 생길 때뿐 아니라 상황이 바뀔 때마다 다시 요청한다.
  문구는 강사 화면의 그 이벤트에 덮어써진다.

  | situation | 언제 | 문구 예 (기본 문구) |
  | --- | --- | --- |
  | `detect` | 새 사건 (선점·대기 순서 포함) | 3번 좌석 쓰러짐 감지 |
  | `again` | 손 내림·일어남 뒤 다시 감지 | 2번 좌석 질문 다시 감지 |
  | `fall_neighbor` | 쓰러짐 15초 미확인 → 옆자리 학생 알림 | 3번 좌석 쓰러짐 15초째 미확인 - 옆자리 학생에게 알림 |
  | `fall_all` | 쓰러짐 30초 미확인 → 전체 알림 | 3번 좌석 쓰러짐 30초째 미확인 - 반 전체에 알림 |
  | `fall_risen` | 일어남 (확인 전) | 3번 좌석 학생 일어남 - 확인 필요 |
  | `hand_down` | 손 내림 | 2번 좌석 손 내림 - 질문 대기 중 |
  | `away_overdue` | 자리 비움 60초 초과 | 4번 좌석 60초째 자리 비움 |

  `seq` 는 요청 번호다. 문구를 만드는 사이 더 새 요청이 나가면 control 이 늦게 온 옛 문구를 버린다.
- `mode` 는 `"gemini"`, `"ollama"`, `"default"` 셋 중 하나만 보낸다. control `db.py` 가 이 세 값만 숫자로 바꾼다.
- Gemini 가 한 번 실패하면 **그 모델을 60초 동안 건너뛰고** 바로 Ollama 로 간다 (죽어 있는 동안 사건마다 5초씩 버리지 않게).
  한도가 모델별이라 건너뛰기도 모델별이다 (알림·질의가 같은 모델이면 같이 쉰다).
- LLM 에는 `kind`, `seat`, `waiting_count` 만 보낸다. 학생 이름 등 개인정보는 보내지 않는다.
- 기획서(Confluence SD-08)에는 "Control 이 LLM 을 부른다"로 되어 있지만, 코드는 SA 그림대로 **Vision 이 부른다.** control 쪽은 받기만 한다.

## 강사 자연어 질의 + tool calling (기능 2, 조회만)

강사가 "오늘 쓰러짐 몇 번?", "지금 대기 중인 거 있어?" 처럼 물으면, LLM 이 **도구를 골라** control 에 조회를 맡기고 결과로 답한다.

```
강사 화면 ──ask {ask_id, text}──▶ control ──ask_request {ask_id, text, now}──▶ vision (ask.py)
                                    ▲  ◀──tool_call {name, args}──── LLM 이 도구 선택 (여러 번 가능)
                                    └──tool_result {result}─────────▶ hub/ask_tools.py 가 DB·상태 조회 (읽기 전용)
강사 화면 ◀──ask_answer────────── control ◀──ask_answer {text, mode, tools, took_s}──
```

| 도구 | 예시 질문 |
| --- | --- |
| `get_open_events` | 지금 대기 중인 요청 있어? 카메라 지금 어디 봐? |
| `search_events(kind, seat, since, until, status, limit)` | 오늘 쓰러짐 몇 번? 3번 좌석 기록, 오감지 몇 건? |
| `get_event_timeline(event_id)` | 7번 이벤트 어떻게 처리됐어? |
| `get_stats(since, until)` | 오늘 질문 평균 몇 초 만에 확인했어? |
| `get_device_status` | 장비 다 연결돼 있어? |
| `get_class_condition(minutes)` | 최근 30분 반 집중도 어때? 졸고 있어? (반 평균 + 세부 6개: 졸음·하품·전방 주시 / 숙임·고개·어깨) |

- LLM 은 SQL 을 쓰지 않는다. 미리 정한 도구만 부르고, control 이 인자를 다시 검사한다.
- **학생 이름은 다루지 않는다** — 좌석 번호로만 답한다 (도구 결과에도 이름 없음).
- **조회만** 한다. "완료 처리해 줘" 같은 요청에는 강사 화면에서 직접 누르라고 안내한다 (조치는 2차).
- **이름을 묻는 질문과 조치 요청은 LLM 에 보내지 않고 정해진 문장으로 답한다** (mode `rule`). 작은 모델이 지어내거나 영어로 답한 일이 있어서.
- 답이 Gemini 가 아니면 못 쓴 이유가 `fallback` 에 담겨 온다 (예: `gemini(...): HTTP 429`, `최근 실패로 60초 동안 건너뜀`). `ask_demo` 는 `⚠` 로 찍는다.
- control 의 도구는 작은 모델의 흔한 실수를 받아 준다: `until="today"` = 오늘 끝, `"yesterday"`, `"now"`.
  `since` 가 미래면 고치라는 오류를 돌려주고, 0건이면 "0건이라고 답한다" 는 note 를 붙인다. 장비 상태는 "3분 전" 처럼 요약한다.
- 순서: Gemini function calling → 실패하면 Ollama `/api/chat` tools → 둘 다 안 되면 "답할 수 없음" (mode `default`).
  Gemini 실패 후 60초 건너뛰기는 알림 문구와 같이 쓴다. 질문 하나 전체 대기 30초 (`ask_timeout_s`).
- 답에는 어떤 도구를 불렀는지(`tools`)가 같이 간다 → 강사 화면에 근거로 보여줄 수 있다.
- Gemini 3.x 는 도구 호출 응답의 `thoughtSignature` 를 다음 요청에 그대로 돌려줘야 한다 → 받은 content 를 그대로 대화에 넣는다.

설정 (`llm/config/assistant.yaml`): `ask_timeout_s`, `ask_max_tool_rounds`, `gemini_ask_timeout_s`, `ollama_ask_timeout_s`,
`gemini_ask_model`(질의용 Gemini, 비우면 `gemini_model`), `ask_ollama_model`(비우면 `ollama_model`. 3b 가 도구를 잘 못 고르면 7b 로), `ask_tool_timeout_s`, `ask_max_chars`, `ask_max_concurrent`.

## 팬틸트로 좌석 보기 (기능 2 확장, 10/22)

"1번 좌석 보여줘", "좌석 2번 비춰줘", "원위치" → 팬틸트가 그 좌석을 비추고 강사 화면 좌석도 자리에 실시간 영상이 열린다.

- 분명한 명령(문장 전체가 "N번 (좌석) 보여줘/비춰줘", "원위치")은 **LLM 없이 규칙으로 바로** 처리한다 (`ask.camera_rule`, Gemini 가 막혀도 동작).
  "카메라를 3번 쪽으로 돌려줄래?" 처럼 다른 표현은 LLM 이 `look_at_seat` / `camera_home` 도구를 고른다.
- 판단은 control `event_manager`: **이벤트를 추적 중이면 거절**, 아니면 LED 끈 채로 이동, **60초 뒤 원위치**, 새 이벤트가 오면 바로 넘김.
- "3번 좌석 오늘 기록 보여줘" 같은 조회 문장은 카메라 명령으로 잡지 않는다 (조회로 감).

## 수업 종료 리포트 (기능 3)

강사가 [수업 종료 리포트] 를 누르면 오늘 하루(0시~지금) 리포트를 만든다.

```
강사 화면 ──report_request──▶ control: 오늘 숫자 집계 (hub/ask_tools.py report_data)
강사 화면 ◀──report (writing, 숫자만)── control ──report_write {data}──▶ vision (report.py): 요약·살펴볼 점
강사 화면 ◀──report (done, 숫자+문장)── control ◀──report_text──────────
```

- **숫자는 control 이 집계한 값이 그대로 화면에 나간다. LLM 은 문장(오늘 요약 2~3문장, 살펴볼 점 0~3개)만 쓴다.**
- 좌석 번호만, 반 평균만. 순서: Gemini(JSON 응답) → Ollama(format=json) → 규칙으로 만든 문장(mode `default`).
- 강사 화면: ⑤ 창의 [수업 종료 리포트] → 요약 카드 [리포트 열기] → 좌석도 자리에 ⑥ 리포트 (PDF 저장·인쇄).
- 반 컨디션 세부(10/22): 리포트 data 의 `condition_detail`(세부 6개 오늘 평균·가장 높았던 시간대)을 근거로 "집중 저하는 주로 졸음, 14시에 50%" 처럼 쓴다. LLM 이 모두 실패하면 규칙 문장도 같은 내용을 넣는다.
- 설정: `report_timeout_s`(40), `ollama_report_timeout_s`(30). 터미널 시험: `python3 llm/ask_demo.py --report`

## AI 조교 관련 파일

`run_assistant.py`(노드 실행)와 `.env`·`.env.example` 은 이 폴더에, AI 조교(LLM) 코드와 설정은 `llm/` 폴더에 있다.
명령은 이 폴더(`service/jarvis_vision_service`)에서 실행한다.

| 파일 | 내용 |
| --- | --- |
| `llm/report.py` | **[리포트]** 요약·살펴볼 점 작성 (`write_report`), `ReportWorker`(report_write 처리) |
| `llm/ask.py` | **[질의]** 도구 정의(`TOOL_DECLS`), Gemini·Ollama tool calling 루프, `AskWorker`(ask_request·tool_result 처리) |
| `llm/ask_demo.py` | **[질의] 터미널 시험.** 강사 화면·vision 역할로 control 에 붙어 질문하고 답·근거 도구를 본다 |
| `llm/alert_demo.py` | **알림 문구 터미널 시험.** vision_service·강사 화면 역할로 control 에 붙어 사건을 넣고 문구를 본다 |
| `run_assistant.py` | 카메라 분석·AI 조교 통합 실행 파일. `explain_request` 를 받으면 별도 스레드에서 `llm.explain_event` 호출 (동시 2개까지). 시작할 때 Ollama 모델을 미리 올림 |
| `llm/llm.py` | Gemini → Ollama → 기본 문구. 프롬프트, 답 다듬기·검사, 단독 시험 명령 |
| `llm/assistant_config.py` | `config/assistant.yaml` + `.env` 읽기 |
| `llm/config/assistant.yaml` | 모델 이름·대기 시간 등 설정 (git 에 올림) |
| `.env.example` | `.env` 틀 (git 에 올림) |
| `.env` | **Gemini API 키. git 에 올리지 않는다** (`.gitignore`) |

AI 조교의 HTTP 호출은 표준 라이브러리 `urllib`을 사용한다. 카메라 분석용 OpenCV·NumPy·Ultralytics 등은 별도로 필요하다.

## AI 조교 처음 설정

### 1. Gemini API 키

[Google AI Studio](https://aistudio.google.com/apikey) 에서 키를 만든 뒤:

```bash
cd service/jarvis_vision_service
cp .env.example .env
nano .env          # GEMINI_API_KEY= 뒤에 키 붙여넣기
```

키를 코드·채팅·커밋에 붙여넣지 않는다. 키가 없으면 Gemini 를 건너뛰고 Ollama 만 쓴다 (동작은 함).

### 2. Ollama (로컬 백업)

```bash
curl -fsSL https://ollama.com/install.sh | sh     # 설치 (서비스로 자동 실행)
ollama pull qwen2.5:3b                            # VRAM 여유가 많으면 qwen2.5:7b
ollama list
```

다른 모델을 쓰면 `llm/config/assistant.yaml` 의 `ollama_model` 을 바꾼다.

### 3. 확인

```bash
python3 llm/assistant_config.py                      # 설정 값, 키 있음/없음 (키 값은 안 보여줌)
python3 llm/llm.py --kind fall --seat 3              # [gemini] 또는 [ollama] 문장이 나오면 OK
```

## AI 조교 설정 (`llm/config/assistant.yaml`)

| 키 | 값 | 설명 |
| --- | --- | --- |
| `gemini_model` | `gemini-3.5-flash-lite` | 알림 문구용. 바꿀 땐 이 줄만 |
| `gemini_timeout_s` | `5` | 넘으면 Ollama 로 (SD-08 기준) |
| `gemini_ask_model` | `gemini-3.5-flash-lite` | 질의용 (비우면 `gemini_model`) |
| `gemini_max_tokens` | `1024` | 작으면 생각하는 모델의 답이 잘린다 |
| `gemini_cooldown_s` | `60` | 실패 후 Gemini 건너뛰는 시간 |
| `gemini_thinking_level` | `""` | 비워 둔다. 넣으면 응답이 멈추는 모델이 있었다 |
| `ollama_model` | `qwen2.5:3b` | |
| `ollama_timeout_s` | `10` | 평소 0.3~0.5초, 첫 호출만 느림 |
| `ollama_warmup` | `true` | 노드 시작 때 모델을 미리 GPU 에 올림 |
| `max_chars` | `120` | 강사 화면에 보낼 최대 글자 수 |
| `max_concurrent` | `2` | 동시에 처리하는 설명 요청 수 |

## AI 조교 시험

### LLM 만 (control 없이)

```bash
python3 llm/llm.py --kind fall --seat 3                         # 실제 순서 그대로
python3 llm/llm.py --kind question --seat 2 --waiting 1
python3 llm/llm.py --kind away --seat 4
python3 llm/llm.py --only ollama --kind fall --seat 3           # 한 단계만
python3 llm/llm.py --only gemini --timeout 30 --repeat 5        # Gemini 를 30초까지 기다리며 5번 → 성공률·시간 요약
python3 llm/llm.py --only gemini --model gemini-3.1-flash-lite --repeat 5   # 다른 모델 비교 (설정 파일은 안 바뀜)
```

### 알림 문구 터미널 시험 (control 과 TCP 로 주고받음)

```bash
# 터미널 1: control
cd service/jarvis_control_service && python3 run_hub.py
# 터미널 2: (run_assistant.py 는 끈 상태로)
cd service/jarvis_vision_service
python3 llm/alert_demo.py --auto      # 정해진 시나리오 약 45초: 질문 → 쓰러짐 선점 → 대기 → 손 내림 → 일어남 → 15/30초 알림 → 완료
python3 llm/alert_demo.py             # 직접 입력: f 3 / q 2 / a 4 (뒤에 end 붙이면 종료), c 1 확인, r 1 완료, d 1 오감지, ls
python3 llm/alert_demo.py -v          # LLM 실패·건너뜀 로그도 보기
```

출력 예:

```
   6.1s  -> 3번 쓰러짐 start
   6.1s     [상태] #1 2번 질문 대기(waiting)
   6.1s     … 문구 요청 #2 새로 감지 (seq 1)
  10.3s  📢 [알림] #2 3번 쓰러짐 · 새로 감지  (gemini 4.2s)
  10.3s            3번 좌석 학생이 쓰러진 것으로 감지되었습니다. 바로 확인해 주세요. 2번 좌석 질문은 잠시 대기합니다.
```

LLM 만 상황별로: `python3 llm/llm.py --kind fall --seat 3 --situation fall_neighbor --elapsed 15`

### 강사 자연어 질의 터미널 시험

```bash
# 터미널 1: control
cd service/jarvis_control_service && python3 run_hub.py
# 터미널 2: (run_assistant.py 는 끈 상태로)
cd service/jarvis_vision_service
python3 llm/ask_demo.py --seed --questions      # 예시 이벤트를 만들고 준비된 질문 10개 → 모드·시간·도구 요약
python3 llm/ask_demo.py                         # 직접 질문. /seed 예시 데이터, /f 3 사건 넣기, /ls 목록, /x 끝
python3 llm/ask_demo.py --ask "오늘 쓰러짐 몇 번 있었어?"
python3 llm/ask_demo.py -v                      # LLM·도구 로그까지
```

출력 예:

```
  16.5s  ❓ 가장 최근에 끝난 이벤트는 어떻게 처리됐어?
  16.5s     🔧 search_events {"status": "closed", "limit": 1} → 3건
  16.5s     🔧 get_event_timeline {"event_id": 4} → 기록 3줄
  21.9s  💬 [gemini 5.4s] 가장 최근에 끝난 것은 1번 좌석 질문(4번)으로, 감지 3초 뒤 오감지로 처리되었습니다.
```

준비된 질문 10개에는 "학생 이름이 뭐야?"(이름을 답하면 안 됨)와 "완료 처리해 줘"(조회만이라 안내해야 함)가 들어 있다.

### 실제 카메라와 AI 조교 통합 확인

앞의 **카메라 실행방법**에 따라 Control·GUI·스트리머·Vision을 실행한다. 손들기로 질문 이벤트를 만들고 Vision의 `-> explain_text event=...` 로그와 GUI의 설명 문구를 확인한다.

현재 `run_assistant.py`는 `--model`과 좌석 설정을 사용하는 통합 실행기다. 과거의 `ra.detect = lambda ...` 주입 예제는 현재 구조에 적용되지 않는다. 카메라 없이 알림·질의만 시험하려면 위 `llm/alert_demo.py` 또는 `llm/ask_demo.py`를 사용하고 통합 Vision은 종료한다.

## AI 조교 로그 읽기

| 로그 | 뜻 |
| --- | --- |
| `설명 gemini(gemini-3.5-flash) (4.2s) ...` | Gemini 성공 |
| `gemini(...) 실패 (6.1s) ... 타임아웃` | 6초 안에 답 없음 → Ollama |
| `gemini(...) 실패 ... HTTP 503: ... high demand` | 구글 서버 혼잡. 잠시 뒤 풀림 |
| `gemini(...) 실패 ... finishReason=MAX_TOKENS` | 답이 길이 제한에 잘림 → `gemini_max_tokens` 늘리기 |
| `... 실패 ... 끝나지 않은 문장 / 한국어가 아닌 답 / 한자가 섞인 답` | 쓸 수 없는 답이라 버림 → 다음 단계 |
| `gemini(...) 건너뜀 (최근 실패, 52초 뒤 다시 시도)` | 실패 후 쉬는 중 |
| `ollama(...) 실패 ... Connection refused` | Ollama 서버 꺼짐 → `sudo systemctl start ollama` |
| `gemini(...) 실패 ... GEMINI_API_KEY 없음` | `.env` 없음 또는 키 비어 있음 |

## 측정 기록 (모델 고른 근거)

아래는 기존 담당자가 기록한 당시 측정·한도 정보다. 현재 카메라 성능 측정이나 API 제공사의 최신 한도 확인 결과는 아니다.

| 날짜 | 모델 | 결과 |
| --- | --- | --- |
| 10/21 오후 | gemini-2.5-flash | 404 (새 사용자 사용 불가) |
| 10/21 오후 | gemini-3.8-flash | generateContent · interactions 모두 응답 없음 |
| 10/21 오후 | gemini-3.5-flash-lite | 한 번 0.8초, 이후 응답 없음 |
| 10/21 오후 | gemini-3.1-flash-lite | 2.5~8초, 30초 무응답·503 섞임. `thinkingConfig` 넣으면 멈춤 |
| 10/22 오전 | gemini-3.5-flash (출력 200토큰) | 답이 잘려 옴 (생각에 길이를 다 씀) |
| 10/22 오전 | **gemini-3.5-flash (1024토큰)** | **5/5 성공, 평균 4.2초 · 최대 4.9초 → 채택** |
| 10/22 오전 | gemini-3.1-flash-lite | 5/5 성공, 평균 9.6초 |
| 10/22 11시 | gemini-3.5-flash | **무료 하루 20회(RPD)·분당 5회 다 씀** → 알림이 전부 Ollama 로 감 (AI Studio 에는 "3.6 Flash" 로 집계) |
| 10/22 11:32 | **gemini-3.5-flash-lite** | **3/3 성공, 평균 0.87초, 무료 하루 500회 → 채택** |
| 10/22 11:32 | gemini-3.1-flash-lite | 3/3 성공, 평균 2.7초 (하루 500회) |
| 10/21 | Ollama qwen2.5:3b | 0.3~0.5초 |

Gemini 응답 속도는 시간대에 따라 크게 달랐다 (10/21 오후는 무응답·503 이 잦았음).
**무료 등급 한도**: flash 는 하루 20회라 수업 한 번도 못 버틴다 → lite(하루 500회)를 쓴다. 한도는 미국 태평양 자정(한국 오후 4시경)에 풀린다.
시험할 때는 `alert_demo.py --alert-ollama`, `ask_demo.py --alert-ollama` 로 알림 문구가 Gemini 한도를 쓰지 않게 할 수 있다.
그래서 대기 시간을 길게 잡지 않고, 실패하면 빨리 Ollama 로 넘기는 쪽을 택했다.

## 팀 확인 필요

- [질의] 강사 화면(instructor_console)에 질문 입력창을 붙일 때 메시지 형식: 보내기 `ask {ask_id, text}`, 받기 `ask_answer {ask_id, text, mode, tools, took_s}`

1. SD-08 의 "Control → Gemini/Ollama" 를 "Vision → Gemini/Ollama" 로 고치기
2. Gemini 모델 = gemini-3.5-flash-lite (무료 하루 500회). 수업에 더 필요하면 유료 등급 검토
3. Ollama 대기 시간 (SD-08 의 "N초") → 10초
4. control 이 보내는 `elapsed_s` 는 이벤트 생성 순간이라 항상 0 이다 (프롬프트에서는 쓰지 않음)
