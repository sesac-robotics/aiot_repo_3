"""
[질의] 강사 자연어 질의 + LLM tool calling (기능 2). 조회 + [수동보기] 팬틸트로 좌석 비추기.

흐름:
    강사 화면 --ask--> control --ask_request {ask_id, text, now}--> vision_service (여기)
        LLM 이 도구를 고르면 --tool_call {name, args}--> control (hub/ask_tools.py 가 DB·상태 조회)
                             <--tool_result {result}--
        (최대 ask_max_tool_rounds 번 반복) 최종 답 --ask_answer {text, mode, tools, took_s}--> control --> 강사 화면

순서: (이름·조치 질문은 규칙으로 바로 답, mode "rule") → Gemini (function calling, gemini_ask_model)
      → 실패하면 Ollama (/api/chat tools, ask_ollama_model) → 그것도 실패하면 기본 답(mode "default").
실패하면 그 모델을 60초 건너뛴다 (llm.py 와 같은 표, 모델별). 못 쓴 이유는 답의 "fallback" 에 담는다.

지키는 것:
- LLM 은 SQL 을 쓰지 않는다. control 에 정해 둔 도구만 부르고, 인자는 control 이 다시 검사한다.
- 학생 이름은 다루지 않는다 (도구 결과에도 없음). 좌석 번호로만 답한다.
- 도구 결과에 없는 것은 지어내지 않고 "기록에 없다" 고 답하게 한다.
"""

import json
import logging
import re
import threading
import time

import llm
from llm import LLMError

log = logging.getLogger("ask")

# ---------------------------------------------------------------- 도구 정의 (control hub/ask_tools.py 와 같아야 함)
_TIME = "지역 시각 'YYYY-MM-DD HH:MM' (예: '2026-10-22 09:00'). '오늘 하루'면 since='today'"
TOOL_DECLS = [
    {
        "name": "get_open_events",
        "description": "지금 열려 있는(처리 중인) 도움 요청 이벤트와 대기열. 카메라가 지금 추적 중인 이벤트도 알려 준다. "
                       "'지금', '현재', '대기 중' 질문에 쓴다.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "search_events",
        "description": "지난 이벤트 기록 검색 (최신 순). 종류·좌석·기간·결과로 거른다. count 는 조건에 맞는 전체 건수.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["fall", "question", "away"],
                     "description": "fall 쓰러짐, question 질문(손 들기), away 자리 비움. 생략하면 전부"},
            "seat": {"type": "integer", "description": "좌석 번호 1~4"},
            "since": {"type": "string", "description": "이 시각 이후 감지된 것. " + _TIME},
            "until": {"type": "string", "description": "이 시각 이전 감지된 것. " + _TIME},
            "status": {"type": "string",
                       "enum": ["open", "closed", "unconfirmed", "resolved", "false_alarm", "expired", "cleared"],
                       "description": "open 처리 중, closed 종료됨, unconfirmed 강사 확인 안 함, resolved 완료, "
                                      "false_alarm 오감지, expired 질문 만료, cleared 자리 복귀"},
            "limit": {"type": "integer", "description": "돌려받을 최대 건수 (1~50, 기본 20)"},
        }},
    },
    {
        "name": "get_event_timeline",
        "description": "이벤트 한 건이 어떻게 처리됐는지 시간 순 기록 (감지, 추적, 선점, 알림, 확인, 완료 등).",
        "parameters": {"type": "object", "properties": {
            "event_id": {"type": "integer", "description": "이벤트 번호"}},
            "required": ["event_id"]},
    },
    {
        "name": "get_stats",
        "description": "기간 안의 이벤트 통계: 종류별 건수, 평균·최대 확인 시간(초), 평균 처리 시간(초), 결과별·좌석별 건수.",
        "parameters": {"type": "object", "properties": {
            "since": {"type": "string", "description": _TIME},
            "until": {"type": "string", "description": _TIME}}},
    },
    {
        "name": "get_device_status",
        "description": "장비 연결 상태: control, vision, 카메라 2대, 팬틸트, 강사 화면, 학생 노트북 1~4. offline 목록 포함.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_class_condition",
        "description": "최근 N분 동안 반 전체 평균 집중 저하·자세 불량 비율(0~1)과 세부 항목 6개"
                       "(졸음·하품·전방 주시 미흡 / 앞으로 숙임·고개 기울임·어깨 비대칭)의 반 평균. "
                       "개인별 값은 없다. '졸고 있어?', '자세 어때?', '반 컨디션' 같은 질문에 쓴다.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "최근 몇 분 (1~240, 기본 30)"}}},
    },
    # ---- [수동보기] 카메라를 움직이는 도구 (조회가 아님. 판단은 control event_manager) ----
    {
        "name": "look_at_seat",
        "description": "팬틸트 카메라로 좌석을 비춘다 (강사 화면에 실시간 영상이 열림, 60초 뒤 원위치). "
                       "도움 요청 이벤트를 추적 중이면 control 이 거절한다(ok=false, reason=tracking). "
                       "'N번 보여줘', 'N번 좌석 비춰 줘', 'N번 자리 카메라로 봐 줘' 같은 요청에 쓴다.",
        "parameters": {"type": "object", "properties": {
            "seat": {"type": "integer", "description": "좌석 번호 1~4"}}, "required": ["seat"]},
    },
    {
        "name": "camera_home",
        "description": "수동으로 돌린 팬틸트 카메라를 원위치로 돌린다. '원위치', '카메라 돌려 놔' 같은 요청에 쓴다.",
        "parameters": {"type": "object", "properties": {}},
    },
]
TOOL_NAMES = {t["name"] for t in TOOL_DECLS}
CAMERA_TOOLS = {"look_at_seat", "camera_home"}
# Gemini 는 인자 없는 함수에 빈 properties 를 주면 거절할 수 있어 parameters 를 뺀다 (Ollama 는 그대로)
GEMINI_DECLS = [{k: v for k, v in t.items() if not (k == "parameters" and not v.get("properties"))}
                for t in TOOL_DECLS]

ASK_SYSTEM = """너는 강의실 AI 조교다. 강사의 질문에 도구로 조회한 실제 기록만 근거로 답한다.
규칙:
- 숫자·시각·좌석은 반드시 도구 결과에서 가져온다. 도구 결과에 없으면 "기록에 없습니다"라고 답하고 지어내지 않는다.
- 필요한 도구를 먼저 부른다. 여러 개가 필요하면 여러 번 불러도 된다. 일반 상식 질문이나 수업과 무관한 질문에는 도구 없이 짧게 답할 수 있는 범위만 답한다.
- 도구 고르기:
  - "지금", "현재", "대기 중" → get_open_events
  - "몇 건", "몇 번", "기록" (종류·좌석·결과별) → search_events 의 count. 예: 오감지 몇 건 → status="false_alarm", 오늘 쓰러짐 → kind="fall", since="today"
  - 평균·최대 시간, 종류별·좌석별 비교 → get_stats
  - "가장 최근", "마지막" 이벤트의 처리 과정 → 먼저 search_events(status="closed", limit=1)로 번호를 찾고 get_event_timeline
  - 장비·연결 → get_device_status (summary 와 offline 을 쓴다) / 반 집중도·자세 → get_class_condition
- 반 컨디션 답: 묶음(집중 저하·자세 불량) 평균을 먼저 말하고, detail 의 avg 에서 가장 높은 세부 항목(top_focus_item, top_posture_item)을 "주로 졸음입니다"처럼 덧붙인다. 비율은 "45%"처럼 쓴다. detail 이 null 이면 세부는 말하지 않는다. "누가 졸아?"처럼 학생을 짚는 질문에는 반 평균만 있어 누구인지는 알 수 없다고 답한다. note 에 보고 학생 수가 적다고 나오면 한 문장으로 알린다.
- 시간 범위가 없는 질문("오감지 몇 건?")은 since/until 을 넣지 않는다. since 에 현재 시각을 넣지 않는다.
- 도구가 error 를 돌려주면 그 안내대로 인자를 고쳐 한 번 더 부른다. count 가 0 이면 "기록이 없습니다"라고 답한다.
- 학생은 좌석 번호로만 부른다 (예: "3번 좌석"). 이름은 모른다. 이름을 물으면 좌석 번호로만 답할 수 있다고 말한다.
- 종류: fall=쓰러짐, question=질문(손 들기), away=자리 비움. 결과: resolved=완료, false_alarm=오감지, expired=만료, cleared=복귀.
- 시간 표현("오늘", "최근 30분", "10시부터")은 질문과 함께 주는 현재 시각 기준으로 since/until 로 바꾼다. "오늘"은 since="today".
- 확인 시간(confirm_after_s)은 감지부터 강사가 확인하기까지, 처리 시간은 감지부터 완료까지다. 초가 60을 넘으면 "1분 20초"처럼 쓴다.
- 할 수 있는 조치는 카메라 돌리기(look_at_seat, camera_home)뿐이다. 확인·완료·오감지 처리를 부탁받으면 강사 화면에서 직접 눌러 달라고 안내한다.
- look_at_seat 결과가 ok=false 면 message 의 이유를 그대로 알린다 (예: 다른 좌석 이벤트를 추적 중). ok=true 면 "N번 좌석을 비추고 있습니다. 60초 뒤 원위치로 돌아갑니다"처럼 알린다.
- 한국어 존댓말로 핵심부터 쓴다. 3~4문장 이내. 건이 많으면 가장 중요한 몇 건만 들고 전체 건수를 말한다. 표나 마크다운, 목록 기호는 쓰지 않는다.
- 장비 질문은 끊긴 장비만 짧게 말한다. 시각은 "10시 31분", 경과는 "3분 전"처럼 쓴다. 영어로 답하지 않는다."""


# ---------------------------------------------------------------- 답 다듬기
def clean_answer(text: str, max_chars: int = 500) -> str:
    text = (text or "").strip()
    text = re.sub(r"\*\*|__|^#+\s*", "", text, flags=re.M)               # 마크다운 강조·제목 기호
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if not text:
        raise LLMError("빈 답")
    if llm._HANZI.search(text):
        raise LLMError(f"한자가 섞인 답: {text[:40]}")
    if not re.search(r"[가-힣]", text):
        raise LLMError(f"한국어가 아닌 답: {text[:40]}")
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip() + "…"
    return text


def summarize_result(result) -> str:
    """도구 결과 한 줄 요약 (강사 화면 '근거' 표시·로그용)."""
    if not isinstance(result, dict):
        return "?"
    if "error" in result:
        return f"오류: {result['error']}"
    if "ok" in result:                                                   # [수동보기] 카메라 도구
        if not result["ok"]:
            return {"tracking": "이벤트 추적 중이라 거절", "no_preset": "각도 미등록", "pantilt_off": "팬틸트 꺼짐",
                    "bad_seat": "좌석 번호 오류"}.get(result.get("reason"), "거절")
        return "원위치" if result.get("home") else f"{result.get('seat')}번으로 이동"
    if "count" in result:
        return f"{result['count']}건"
    if "total" in result:
        return f"총 {result['total']}건"
    if "timeline" in result:
        return f"기록 {len(result['timeline'])}줄"
    if "devices" in result:
        off = result.get("offline") or []
        return f"끊김 {len(off)}개" if off else "모두 연결"
    if "samples" in result:
        return f"집계 {result['samples']}개"
    return "ok"


def _user_text(question: str, now: str | None) -> str:
    return f"[현재 시각] {now or time.strftime('%Y-%m-%d %H:%M:%S')}\n[질문] {question}"


# ---------------------------------------------------------------- Gemini
def _gemini_loop(question, now, call_tool, cfg, deadline, used):
    key = cfg.get("gemini_api_key")
    if not key:
        raise LLMError("GEMINI_API_KEY 없음")
    url = cfg.get("gemini_url", llm.GEMINI_URL).format(model=_gemini_model(cfg))
    contents = [{"role": "user", "parts": [{"text": _user_text(question, now)}]}]
    rounds = int(cfg.get("ask_max_tool_rounds", 5))
    for i in range(rounds + 1):
        left = deadline - time.monotonic()
        if left < 1:
            raise LLMError("시간 초과 (전체 대기 시간)")
        body = {
            "systemInstruction": {"parts": [{"text": ASK_SYSTEM}]},
            "contents": contents,
            "tools": [{"functionDeclarations": GEMINI_DECLS}],
            "generationConfig": {"temperature": 0.2,
                                 "maxOutputTokens": int(cfg.get("gemini_max_tokens", 1024))},
        }
        data = llm._post_json(url, body, min(left, float(cfg.get("gemini_ask_timeout_s", 15))),
                              headers={"x-goog-api-key": key})
        try:
            cand = data["candidates"][0]
            content = cand.get("content") or {}
            parts = content.get("parts") or []
        except (KeyError, IndexError, TypeError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or str(data)[:120]
            raise LLMError(f"답 없음: {reason}") from None
        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if calls:
            if i == rounds:
                raise LLMError(f"도구 호출이 {rounds}번을 넘음")
            contents.append(content)                    # 받은 그대로 (thoughtSignature 를 다음 요청에 돌려줘야 함)
            responses = []
            for c in calls:
                result = _run_tool(call_tool, c.get("name"), c.get("args") or {}, used)
                fr = {"name": c.get("name"), "response": {"result": result}}
                if c.get("id"):
                    fr["id"] = c["id"]
                responses.append({"functionResponse": fr})
            contents.append({"role": "user", "parts": responses})
            continue
        finish = cand.get("finishReason", "STOP")
        if finish != "STOP":
            raise LLMError(f"답이 끝까지 안 옴 (finishReason={finish})")
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return clean_answer(text, int(cfg.get("ask_max_chars", 500)))
    raise LLMError("도구 호출 반복 초과")


# ---------------------------------------------------------------- Ollama
_JSON_CALL = re.compile(r"\{.*\}", re.S)


def _parse_text_call(content: str):
    """작은 모델이 tool_calls 대신 본문에 {"name":..,"arguments":..} 를 쓰는 경우를 살린다."""
    m = _JSON_CALL.search(content or "")
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    if isinstance(obj, dict) and obj.get("name") in TOOL_NAMES:
        return [{"function": {"name": obj["name"], "arguments": obj.get("arguments") or obj.get("parameters") or {}}}]
    return None


def _ollama_loop(question, now, call_tool, cfg, deadline, used):
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat"
    messages = [{"role": "system", "content": ASK_SYSTEM},
                {"role": "user", "content": _user_text(question, now)}]
    tools = [{"type": "function", "function": t} for t in TOOL_DECLS]
    rounds = int(cfg.get("ask_max_tool_rounds", 5))
    for i in range(rounds + 1):
        left = deadline - time.monotonic()
        if left < 1:
            raise LLMError("시간 초과 (전체 대기 시간)")
        body = {"model": _ollama_model(cfg), "messages": messages,
                "tools": tools, "stream": False, "keep_alive": cfg.get("ollama_keep_alive", "30m"),
                "options": {"temperature": 0.2, "num_predict": 400}}
        data = llm._post_json(url, body, min(left, float(cfg.get("ollama_ask_timeout_s", 20))))
        if "error" in data:
            raise LLMError(f"ollama: {data['error']}")
        msg = data.get("message") or {}
        calls = msg.get("tool_calls") or _parse_text_call(msg.get("content", ""))
        if calls:
            if i == rounds:
                raise LLMError(f"도구 호출이 {rounds}번을 넘음")
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
            for c in calls:
                fn = c.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = _run_tool(call_tool, fn.get("name"), args, used)
                messages.append({"role": "tool", "tool_name": fn.get("name"),
                                 "content": json.dumps(result, ensure_ascii=False)})
            continue
        return clean_answer(msg.get("content", ""), int(cfg.get("ask_max_chars", 500)))
    raise LLMError("도구 호출 반복 초과")


def _run_tool(call_tool, name, args, used: list) -> dict:
    if name not in TOOL_NAMES:
        result = {"error": f"모르는 도구: {name}"}
    else:
        t0 = time.time()
        result = call_tool(name, args if isinstance(args, dict) else {})
        log.info("도구 %s %s -> %s (%.2fs)", name, args, summarize_result(result), time.time() - t0)
    used.append({"name": str(name), "args": args if isinstance(args, dict) else {},
                 "summary": summarize_result(result),
                 **({"ok": bool(result.get("ok"))} if name in CAMERA_TOOLS else {})})   # [수동보기] 화면의 [원위치] 버튼용
    return result


# ---------------------------------------------------------------- 규칙으로 바로 답하는 질문
# LLM 이 틀리면 개인정보·잘못된 조치 안내가 되는 질문은 LLM 에 보내지 않고 정해진 문장으로 답한다.
_NAME_Q = re.compile(r"이름|성함|실명")
_ACTION_Q = re.compile(r"(완료|오감지|종료|해결|확인)\s*(으로|로)?\s*처리(?!된|됐|되|한 |했)"
                       r"|(완료|종료|해결)\s*(해|시켜|하자)(?!서)"
                       r"|(닫아|꺼)\s*(줘|주세요|라)")
RULE_ANSWERS = {
    "name": "학생 이름은 AI 조교가 다루지 않습니다. 좌석 번호로 물어봐 주세요 (예: \"2번 좌석 오늘 기록\"). "
            "이름은 강사 화면 좌석도에서 확인할 수 있습니다.",
    "action": "AI 조교는 조회와 카메라 돌리기만 할 수 있습니다. 확인·완료·오감지는 강사 화면의 이벤트에서 직접 눌러 주세요.",
}


# [수동보기] 분명한 카메라 명령은 LLM 없이 바로 (Gemini 가 막혀도 동작, 빠름). 문장 전체가 명령일 때만.
_VIEW_Q = re.compile(r"^\s*(?:(?:좌석|자리)\s*)?(?P<seat>[1-4])\s*번\s*(좌석|자리|학생)?\s*(을|를|좀|쪽)?\s*(카메라로\s*)?"
                     r"(보여|비춰|비추|봐|확대)\s*(줘|주세요|줄래|봐|라)?\s*[.?!~]*\s*$")
_HOME_Q = re.compile(r"^\s*(카메라\s*)?(원위치|원래\s*자리|홈으로)\s*(로|으로)?\s*(돌려|해|가)?\s*(줘|주세요)?\s*[.?!~]*\s*$")


def camera_rule(question: str, call_tool) -> dict | None:
    """'1번 보여줘' / '원위치' 를 바로 처리한다. 해당 없으면 None."""
    m = _VIEW_Q.match(question)
    name, args = (("look_at_seat", {"seat": int(m.group("seat"))}) if m else
                  (("camera_home", {}) if _HOME_Q.match(question) else (None, None)))
    if name is None:
        return None
    used: list = []
    r = _run_tool(call_tool, name, args, used)
    if r.get("ok") and r.get("home"):
        text = "카메라를 원위치로 돌렸습니다." if not r.get("note") else "수동으로 돌린 카메라가 없어 이미 원위치입니다."
    elif r.get("ok"):
        text = f"{r['seat']}번 좌석을 비추고 있습니다. {int(r.get('hold_s', 60))}초 뒤 원위치로 돌아갑니다."
    elif r.get("reason") == "tracking":
        t = r.get("tracking") or {}
        kind = {"fall": "쓰러짐", "question": "질문", "away": "자리비움"}.get(t.get("kind"), "")
        text = (f"지금 {t.get('seat')}번 좌석 {kind} 이벤트를 추적 중이라 카메라를 돌릴 수 없습니다. "
                "이벤트를 완료한 뒤 다시 요청해 주세요.")
    else:
        text = f"카메라를 돌리지 못했습니다. {r.get('message') or r.get('error') or ''}".strip()
    return {"text": text, "mode": "rule", "tools": used, "took_s": 0.0, "fallback": []}


def rule_answer(question: str) -> str | None:
    if _NAME_Q.search(question):
        return RULE_ANSWERS["name"]
    if _ACTION_Q.search(question):
        return RULE_ANSWERS["action"]
    return None


def _gemini_model(cfg: dict) -> str:
    return cfg.get("gemini_ask_model") or cfg["gemini_model"]


def _ollama_model(cfg: dict) -> str:
    return cfg.get("ask_ollama_model") or cfg["ollama_model"]


# ---------------------------------------------------------------- 순서대로
def answer(question: str, now: str | None, call_tool, cfg: dict, only: str | None = None) -> dict:
    """{"text", "mode": gemini|ollama|rule|default, "tools": [{name,args,summary}], "took_s", "fallback": [이유]}.
    예외를 던지지 않는다. call_tool(name, args) -> dict 는 control 에 도구를 실행시키는 함수.
    fallback: 앞 단계(Gemini 등)를 못 쓴 이유 — 강사 화면·터미널에 보여 준다."""
    t0 = time.monotonic()
    rule = rule_answer(question) if not only else None
    if rule:
        return {"text": rule, "mode": "rule", "tools": [], "took_s": 0.0, "fallback": []}
    cam = camera_rule(question, call_tool) if not only else None           # [수동보기]
    if cam:
        cam["took_s"] = round(time.monotonic() - t0, 1)
        return cam
    deadline = t0 + float(cfg.get("ask_timeout_s", 30))
    errors = []
    for mode, loop, enabled, cooldown, model in (
            ("gemini", _gemini_loop, cfg.get("gemini_enabled", True), cfg.get("gemini_cooldown_s", 60), _gemini_model(cfg)),
            ("ollama", _ollama_loop, cfg.get("ollama_enabled", True), 0, _ollama_model(cfg))):
        if not enabled or (only and only != mode):
            continue
        name = f"{mode}({model})"                       # 실패 쉬기는 모델별 (알림 문구와 같은 모델이면 같이 쉼)
        left = llm._skipping(name) if not only else 0.0
        if left:
            errors.append(f"{name}: 최근 실패로 {left:.0f}초 동안 건너뜀")
            log.info("%s 건너뜀 (최근 실패, %.0f초 뒤 다시 시도)", name, left)
            continue
        used: list = []
        try:
            text = loop(question, now, call_tool, cfg, deadline, used)
            llm._mark_ok(name)
            took = time.monotonic() - t0
            log.info("답 %s (%.1fs, 도구 %d번): %s", name, took, len(used), text[:80])
            return {"text": text, "mode": mode, "tools": used, "took_s": round(took, 1), "fallback": errors}
        except LLMError as e:
            llm._mark_failed(name, float(cooldown))
            errors.append(f"{name}: {e}")
            log.warning("%s 실패 (%.1fs): %s", name, time.monotonic() - t0, e)
        except Exception as e:                              # 예상 못 한 오류도 다음 단계로
            llm._mark_failed(name, float(cooldown))
            errors.append(f"{name}: {type(e).__name__}")
            log.exception("%s 처리 중 오류", name)
    return {"text": "지금은 AI 조교가 답할 수 없습니다. 잠시 뒤 다시 물어봐 주세요.",
            "mode": "default", "tools": [], "took_s": round(time.monotonic() - t0, 1),
            "fallback": errors or ["사용할 수 있는 LLM 없음"]}


# ---------------------------------------------------------------- control 과 연결
class ToolBridge:
    """tool_call 을 control 에 보내고 tool_result 를 기다린다 (LLM 스레드에서 부름)."""

    def __init__(self, client, timeout_s: float = 5.0):
        self.client = client
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._next = 0
        self._waiting: dict[int, list] = {}           # call_id -> [Event, result]

    def call(self, ask_id: int, name: str, args: dict) -> dict:
        with self._lock:
            self._next += 1
            call_id = self._next
            slot = self._waiting[call_id] = [threading.Event(), None]
        try:
            if not self.client.send("tool_call", value={"ask_id": ask_id, "call_id": call_id,
                                                        "name": name, "args": args}):
                return {"error": "control 과 연결이 끊겨 조회하지 못함"}
            if not slot[0].wait(self.timeout_s):
                return {"error": "control 의 조회 응답 시간 초과"}
            return slot[1] if isinstance(slot[1], dict) else {"error": "조회 결과 형식 오류"}
        finally:
            with self._lock:
                self._waiting.pop(call_id, None)

    def on_tool_result(self, v: dict):
        with self._lock:
            slot = self._waiting.get(v.get("call_id"))
        if slot is not None:
            slot[1] = v.get("result")
            slot[0].set()


class AskWorker:
    """vision_service 노드에서 ask_request / tool_result 를 처리한다. run_assistant.py·ask_demo.py 가 같이 쓴다."""

    def __init__(self, client, cfg: dict, on_answer=None):
        self.client = client
        self.cfg = cfg
        self.bridge = ToolBridge(client, float(cfg.get("ask_tool_timeout_s", 5)))
        self.slots = threading.Semaphore(int(cfg.get("ask_max_concurrent", 1)))
        self.on_answer = on_answer                      # (req, result) -> None, 터미널 표시용

    def on_message(self, msg: dict) -> bool:
        """처리했으면 True."""
        t, v = msg["type"], msg["value"]
        if t == "ask_request":
            threading.Thread(target=self.handle, args=(v,), daemon=True).start()
            return True
        if t == "tool_result":
            self.bridge.on_tool_result(v)
            return True
        return False

    def handle(self, req: dict):
        with self.slots:
            try:
                res = answer(req["text"], req.get("now"),
                             lambda name, args: self.bridge.call(req["ask_id"], name, args), self.cfg)
            except Exception:                           # 혹시 모를 오류에도 답은 꼭 보낸다
                log.exception("질의 처리 실패 #%s", req.get("ask_id"))
                res = {"text": "질문을 처리하다 오류가 났습니다.", "mode": "default", "tools": [], "took_s": 0.0,
                       "fallback": ["vision 내부 오류"]}
        self.client.send("ask_answer", value={"ask_id": req["ask_id"], **res})
        if self.on_answer:
            self.on_answer(req, res)
