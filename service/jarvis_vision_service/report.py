"""
[리포트] 수업 종료 리포트의 문장 쓰기 (기능 3).

control 이 오늘 하루 숫자(hub/ask_tools.py report_data)를 모아 report_write {report_id, data, now} 로 보내면,
여기서 LLM 으로 "오늘 요약"(2~3문장)과 "다음 수업에 살펴볼 점"(최대 3개)만 써서 report_text 로 돌려준다.

- 숫자는 화면에 control 이 집계한 값이 그대로 나간다. LLM 은 문장만 쓴다 → 리포트 숫자를 지어낼 수 없다.
- 좌석 번호만, 반 평균만 (data 에 이름·개인 값이 없다).
- 순서: Gemini (gemini_ask_model, JSON 응답) → Ollama (format=json) → 규칙으로 만든 문장 (mode "default").
  실패 후 60초 건너뛰기는 llm.py·ask.py 와 같은 표(모델별).
"""

import json
import logging
import threading
import time

import llm
from ask import _gemini_model, _ollama_model
from llm import LLMError

log = logging.getLogger("report")

KIND_KO = {"fall": "쓰러짐", "question": "질문", "away": "자리비움"}

REPORT_SYSTEM = """너는 강의실 AI 조교다. 강사에게 보여줄 수업 종료 리포트의 문장 부분을 쓴다.
입력은 오늘 하루 기록을 집계한 JSON 이다. 이 JSON 에 있는 사실만 쓴다.
규칙:
- 숫자·시각·좌석은 JSON 값을 그대로 쓴다. 없는 숫자를 만들거나 계산해서 새 숫자를 쓰지 않는다.
- 학생은 좌석 번호로만 부른다. 개인의 집중·자세는 쓰지 않는다 (반 평균 hourly_condition 만 있다).
- summary: 오늘을 2~3문장으로 요약한다. 전체 건수와 종류, 확인 시간, 눈에 띄는 일(오감지, 쓰러짐 단계 알림, 장비 끊김, 반 평균 변화)을 고른다.
- suggestions: 다음 수업에 살펴볼 점 0~3개. 각각 한 문장. JSON 의 근거(시각·좌석·값)를 넣고, 그 근거가 없으면 쓰지 않는다. 일반론("집중을 높이세요")은 쓰지 않는다.
- 기록이 거의 없으면(total 0~1) 그렇다고 짧게 쓰고 suggestions 는 비워도 된다.
- 한국어 존댓말. 마크다운·목록 기호 없이 문장만.
- 출력은 JSON 하나: {"summary": "...", "suggestions": ["...", "..."]}"""


# ---------------------------------------------------------------- 규칙으로 만든 문장 (LLM 모두 실패)
def default_text(d: dict) -> tuple[str, list[str]]:
    k = d.get("by_kind") or {}
    total = d.get("total", 0)
    if not total:
        return "오늘은 기록된 도움 요청이 없습니다.", []
    kinds = " · ".join(f"{KIND_KO[x]} {k.get(x, 0)}" for x in ("fall", "question", "away"))
    s = f"오늘 도움 요청은 {total}건({kinds})이었습니다."
    if d.get("avg_confirm"):
        s += f" 강사 확인까지 평균 {d['avg_confirm']}, 가장 길었던 때는 {d.get('max_confirm')}였습니다."
    sug = []
    for fa in (d.get("false_alarms") or [])[:1]:
        sug.append(f"{fa['at']} {fa['seat']}번 좌석 {KIND_KO.get(fa['kind'], fa['kind'])}이 오감지로 처리되었습니다. "
                   "같은 일이 반복되면 감지 기준을 점검해 보세요.")
    case = d.get("max_confirm_case")
    if case and (d.get("max_confirm_s") or 0) >= 30:                 # 확인이 30초 넘게 걸린 때
        sug.append(f"{case['at']} {case['seat']}번 좌석 {KIND_KO.get(case['kind'], case['kind'])} 확인에 "
                   f"{d['max_confirm']}가 걸렸습니다.")
    for o in (d.get("device_outages") or [])[:1]:
        sug.append(f"{o['from']}~{o['to']} {o['name']} 연결이 끊겼습니다 ({o['duration']}).")
    return s, sug[:3]


# ---------------------------------------------------------------- 답 검사
def _parse(text: str, max_chars: int = 400) -> tuple[str, list[str]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    try:
        obj = json.loads(text[text.find("{"): text.rfind("}") + 1])
    except ValueError:
        raise LLMError(f"JSON 아님: {text[:60]}") from None
    summary = obj.get("summary") if isinstance(obj, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        raise LLMError("summary 없음")
    out = []
    for x in [summary] + [s for s in (obj.get("suggestions") or []) if isinstance(s, str)][:3]:
        x = " ".join(x.split()).strip("-•* ")
        if llm._HANZI.search(x):
            raise LLMError(f"한자가 섞인 답: {x[:40]}")
        if not any("가" <= ch <= "힣" for ch in x):
            raise LLMError(f"한국어가 아닌 답: {x[:40]}")
        out.append(x[:max_chars])
    return out[0], [s for s in out[1:] if s]


def _user_text(data: dict) -> str:
    return "오늘 기록 집계 JSON:\n" + json.dumps(data, ensure_ascii=False)


def _gemini(data: dict, cfg: dict, timeout: float) -> tuple[str, list[str]]:
    key = cfg.get("gemini_api_key")
    if not key:
        raise LLMError("GEMINI_API_KEY 없음")
    url = cfg.get("gemini_url", llm.GEMINI_URL).format(model=_gemini_model(cfg))
    body = {"systemInstruction": {"parts": [{"text": REPORT_SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": _user_text(data)}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": int(cfg.get("gemini_max_tokens", 1024)),
                                 "responseMimeType": "application/json"}}
    resp = llm._post_json(url, body, timeout, headers={"x-goog-api-key": key})
    try:
        cand = resp["candidates"][0]
        parts = cand["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"답 없음: {str(resp)[:120]}") from None
    if cand.get("finishReason", "STOP") != "STOP":
        raise LLMError(f"답이 끝까지 안 옴 (finishReason={cand.get('finishReason')})")
    return _parse("".join(p.get("text", "") for p in parts if not p.get("thought")))


def _ollama(data: dict, cfg: dict, timeout: float) -> tuple[str, list[str]]:
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"
    body = {"model": _ollama_model(cfg), "system": REPORT_SYSTEM, "prompt": _user_text(data), "format": "json",
            "stream": False, "keep_alive": cfg.get("ollama_keep_alive", "30m"),
            "options": {"temperature": 0.3, "num_predict": 600}}
    resp = llm._post_json(url, body, timeout)
    if "error" in resp:
        raise LLMError(f"ollama: {resp['error']}")
    return _parse(resp.get("response", ""))


def write_report(data: dict, cfg: dict) -> dict:
    """{"summary", "suggestions", "mode": gemini|ollama|default, "took_s", "fallback"}. 예외를 던지지 않는다."""
    t0 = time.monotonic()
    deadline = t0 + float(cfg.get("report_timeout_s", 40))
    errors = []
    for mode, fn, enabled, cooldown, model, per in (
            ("gemini", _gemini, cfg.get("gemini_enabled", True), cfg.get("gemini_cooldown_s", 60), _gemini_model(cfg),
             float(cfg.get("gemini_ask_timeout_s", 15))),
            ("ollama", _ollama, cfg.get("ollama_enabled", True), 0, _ollama_model(cfg),
             float(cfg.get("ollama_report_timeout_s", 30)))):
        if not enabled:
            continue
        name = f"{mode}({model})"
        left = llm._skipping(name)
        if left:
            errors.append(f"{name}: 최근 실패로 {left:.0f}초 동안 건너뜀")
            continue
        remain = deadline - time.monotonic()
        if remain < 1:
            errors.append(f"{name}: 시간 초과 (전체 대기 시간)")
            break
        try:
            summary, sugs = fn(data, cfg, min(per, remain))
            llm._mark_ok(name)
            took = round(time.monotonic() - t0, 1)
            log.info("리포트 문장 %s (%.1fs): %s", name, took, summary[:80])
            return {"summary": summary, "suggestions": sugs, "mode": mode, "took_s": took, "fallback": errors}
        except LLMError as e:
            llm._mark_failed(name, float(cooldown))
            errors.append(f"{name}: {e}")
            log.warning("리포트 %s 실패: %s", name, e)
        except Exception as e:
            llm._mark_failed(name, float(cooldown))
            errors.append(f"{name}: {type(e).__name__}")
            log.exception("리포트 %s 오류", name)
    summary, sugs = default_text(data)
    return {"summary": summary, "suggestions": sugs, "mode": "default",
            "took_s": round(time.monotonic() - t0, 1), "fallback": errors or ["사용할 수 있는 LLM 없음"]}


class ReportWorker:
    """vision_service 노드에서 report_write 를 처리한다. run_assistant.py·ask_demo.py 가 같이 쓴다."""

    def __init__(self, client, cfg: dict, on_done=None):
        self.client, self.cfg, self.on_done = client, cfg, on_done
        self.slots = threading.Semaphore(1)

    def on_message(self, msg: dict) -> bool:
        if msg["type"] != "report_write":
            return False
        threading.Thread(target=self.handle, args=(msg["value"],), daemon=True).start()
        return True

    def handle(self, req: dict):
        with self.slots:
            try:
                res = write_report(req.get("data") or {}, self.cfg)
            except Exception:
                log.exception("리포트 처리 실패 #%s", req.get("report_id"))
                s, sug = default_text(req.get("data") or {})
                res = {"summary": s, "suggestions": sug, "mode": "default", "took_s": 0.0,
                       "fallback": ["vision 내부 오류"]}
        self.client.send("report_text", value={"report_id": req["report_id"], **res})
        if self.on_done:
            self.on_done(req, res)
