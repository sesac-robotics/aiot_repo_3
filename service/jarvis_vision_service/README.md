# jarvis_vision_service (vision_service 노드)

강사 노트북에서 도는 Vision 노드. 두 카메라 영상을 보고 사건(쓰러짐·질문·자리 비움)을 control 에 보내고,
추적 오차를 보내고, **AI 조교 상황 설명(LLM)** 을 만든다.

이 문서는 지금 구현된 **AI 조교(LLM) 부분** 위주다. 감지(`detect`)·추적(`find_offset`)은 아직 TODO.

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
| `get_class_condition(minutes)` | 최근 30분 반 집중도 어때? (반 평균만) |

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

설정 (`config/assistant.yaml`): `ask_timeout_s`, `ask_max_tool_rounds`, `gemini_ask_timeout_s`, `ollama_ask_timeout_s`,
`gemini_ask_model`(질의용 Gemini, 비우면 `gemini_model`), `ask_ollama_model`(비우면 `ollama_model`. 3b 가 도구를 잘 못 고르면 7b 로), `ask_tool_timeout_s`, `ask_max_chars`, `ask_max_concurrent`.

## 파일

| 파일 | 내용 |
| --- | --- |
| `ask.py` | **[질의]** 도구 정의(`TOOL_DECLS`), Gemini·Ollama tool calling 루프, `AskWorker`(ask_request·tool_result 처리) |
| `ask_demo.py` | **[질의] 터미널 시험.** 강사 화면·vision 역할로 control 에 붙어 질문하고 답·근거 도구를 본다 |
| `alert_demo.py` | **알림 문구 터미널 시험.** vision_service·강사 화면 역할로 control 에 붙어 사건을 넣고 문구를 본다 |
| `run_assistant.py` | 노드 실행 파일. `explain_request` 를 받으면 별도 스레드에서 `llm.explain_event` 호출 (동시 2개까지). 시작할 때 Ollama 모델을 미리 올림 |
| `llm.py` | Gemini → Ollama → 기본 문구. 프롬프트, 답 다듬기·검사, 단독 시험 명령 |
| `assistant_config.py` | `config/assistant.yaml` + `.env` 읽기 |
| `config/assistant.yaml` | 모델 이름·대기 시간 등 설정 (git 에 올림) |
| `.env.example` | `.env` 틀 (git 에 올림) |
| `.env` | **Gemini API 키. git 에 올리지 않는다** (`.gitignore`) |

외부 패키지는 쓰지 않는다 (표준 라이브러리 `urllib` 만). `requirements.txt` 변경 없음.

## 처음 설정

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

다른 모델을 쓰면 `config/assistant.yaml` 의 `ollama_model` 을 바꾼다.

### 3. 확인

```bash
python3 assistant_config.py                      # 설정 값, 키 있음/없음 (키 값은 안 보여줌)
python3 llm.py --kind fall --seat 3              # [gemini] 또는 [ollama] 문장이 나오면 OK
```

## 설정 (`config/assistant.yaml`)

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

## 시험

### LLM 만 (control 없이)

```bash
python3 llm.py --kind fall --seat 3                         # 실제 순서 그대로
python3 llm.py --kind question --seat 2 --waiting 1
python3 llm.py --kind away --seat 4
python3 llm.py --only ollama --kind fall --seat 3           # 한 단계만
python3 llm.py --only gemini --timeout 30 --repeat 5        # Gemini 를 30초까지 기다리며 5번 → 성공률·시간 요약
python3 llm.py --only gemini --model gemini-3.1-flash-lite --repeat 5   # 다른 모델 비교 (설정 파일은 안 바뀜)
```

### 알림 문구 터미널 시험 (control 과 TCP 로 주고받음)

```bash
# 터미널 1: control
cd service/jarvis_control_service && python3 run_hub.py
# 터미널 2: (run_assistant.py 는 끈 상태로)
cd service/jarvis_vision_service
python3 alert_demo.py --auto      # 정해진 시나리오 약 45초: 질문 → 쓰러짐 선점 → 대기 → 손 내림 → 일어남 → 15/30초 알림 → 완료
python3 alert_demo.py             # 직접 입력: f 3 / q 2 / a 4 (뒤에 end 붙이면 종료), c 1 확인, r 1 완료, d 1 오감지, ls
python3 alert_demo.py -v          # LLM 실패·건너뜀 로그도 보기
```

출력 예:

```
   6.1s  -> 3번 쓰러짐 start
   6.1s     [상태] #1 2번 질문 대기(waiting)
   6.1s     … 문구 요청 #2 새로 감지 (seq 1)
  10.3s  📢 [알림] #2 3번 쓰러짐 · 새로 감지  (gemini 4.2s)
  10.3s            3번 좌석 학생이 쓰러진 것으로 감지되었습니다. 바로 확인해 주세요. 2번 좌석 질문은 잠시 대기합니다.
```

LLM 만 상황별로: `python3 llm.py --kind fall --seat 3 --situation fall_neighbor --elapsed 15`

### 강사 자연어 질의 터미널 시험

```bash
# 터미널 1: control
cd service/jarvis_control_service && python3 run_hub.py
# 터미널 2: (run_assistant.py 는 끈 상태로)
cd service/jarvis_vision_service
python3 ask_demo.py --seed --questions      # 예시 이벤트를 만들고 준비된 질문 10개 → 모드·시간·도구 요약
python3 ask_demo.py                         # 직접 질문. /seed 예시 데이터, /f 3 사건 넣기, /ls 목록, /x 끝
python3 ask_demo.py --ask "오늘 쓰러짐 몇 번 있었어?"
python3 ask_demo.py -v                      # LLM·도구 로그까지
```

출력 예:

```
  16.5s  ❓ 가장 최근에 끝난 이벤트는 어떻게 처리됐어?
  16.5s     🔧 search_events {"status": "closed", "limit": 1} → 3건
  16.5s     🔧 get_event_timeline {"event_id": 4} → 기록 3줄
  21.9s  💬 [gemini 5.4s] 가장 최근에 끝난 것은 1번 좌석 질문(4번)으로, 감지 3초 뒤 오감지로 처리되었습니다.
```

준비된 질문 10개에는 "학생 이름이 뭐야?"(이름을 답하면 안 됨)와 "완료 처리해 줘"(조회만이라 안내해야 함)가 들어 있다.

### control 과 연결 (터미널 3개, 실제 노드)

```bash
# 1: control
cd service/jarvis_control_service && python3 run_hub.py
# 2: 강사 화면 흉내
cd service/jarvis_control_service && python3 -m tools.fake_node --node instructor_console --server 127.0.0.1
# 3: vision_service — 감지 대신 3초 뒤 2번 좌석 질문을 일부러 발생
cd service/jarvis_vision_service && python3 -c '
import sys, time; sys.argv=["run_assistant.py","--server","127.0.0.1"]
import run_assistant as ra; t0=time.time()
ra.detect = lambda: {(2,"question"): True} if time.time()-t0 > 3 else {}
ra.main()'
```

3번 터미널에 `-> explain_text event=N [gemini] ...`, 2번 터미널의 `event_update` 에 같은 `explain_text` 가 보이면 OK.

## 로그 읽기

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
