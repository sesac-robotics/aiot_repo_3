"""
AI 조교 상황 설명 (LLM). 기준: Confluence SD-08 AI 상황 설명.

    text, mode = explain_event(req, cfg)
      req  = control 의 explain_request value {"event_id","kind","seat","elapsed_s","waiting_count"}
      mode = "gemini" | "ollama" | "default"   ← control db.py 가 이 세 값만 받는다 (다른 값이면 KeyError)

순서: Gemini (5초) → 실패·타임아웃·빈 답이면 Ollama (로컬) → 그것도 안 되면 기본 문구.
Gemini 가 실패하면 gemini_cooldown_s(기본 60초) 동안은 Gemini 를 건너뛰고 바로 Ollama 로 간다.
어떤 경우에도 예외를 밖으로 던지지 않고 항상 (문자열, mode) 를 돌려준다.
외부 패키지 없이 표준 라이브러리(urllib)만 쓴다.

개인정보(학생 이름 등)는 LLM 에 보내지 않는다. 보내는 것은 kind, seat, waiting_count 뿐.

단독 시험:
    python3 llm.py --kind fall --seat 3
    python3 llm.py --kind question --seat 2 --waiting 1 --only ollama
    python3 llm.py --warmup
    python3 llm.py --only gemini --timeout 20 --repeat 5     # Gemini 만, 20초까지 기다리며 5번
    python3 llm.py --only gemini --model gemini-3.5-flash --timeout 15
"""

import argparse
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

KIND_KO = {"fall": "쓰러짐", "question": "질문(손 들기)", "away": "자리 비움"}
DEFAULT_KIND_KO = {"fall": "쓰러짐", "question": "질문", "away": "자리 비움"}

SYSTEM_PROMPT = """너는 강의실 AI 조교다. 카메라가 감지한 상황을 강사에게 알리는 짧은 설명을 쓴다.
규칙:
- 한국어 존댓말로 한두 문장만 쓴다. 문장만 쓰고 따옴표, 제목, 머리말, 목록 기호는 붙이지 않는다.
- 주어진 정보(종류, 좌석, 대기 건수)만 쓴다. 원인·이름·건강 상태를 추측하지 않는다.
- 너는 알리기만 한다. "구급차를 부르겠습니다", "가겠습니다"처럼 직접 행동하겠다고 쓰지 않는다.
- 종류별로 쓸 말:
  - 쓰러짐: 쓰러진 것으로 감지되었다고 알리고 "바로 확인해 주세요"를 붙인다.
  - 질문(손 들기): 손을 들고 질문을 기다린다고 알린다. "바로 확인해 주세요"는 쓰지 않는다.
  - 자리 비움: 자리를 비웠다고만 알린다. "바로 확인해 주세요"는 쓰지 않는다.
- 대기 중인 다른 요청이 1건 이상이면 끝에 "대기 중인 요청이 N건 더 있습니다."를 붙인다. 0건이면 대기 이야기는 쓰지 않는다.

예시:
상황: 종류=쓰러짐, 좌석=3번, 대기 중인 다른 요청=0건
설명: 3번 좌석 학생이 쓰러진 것으로 감지되었습니다. 바로 확인해 주세요.

상황: 종류=질문(손 들기), 좌석=2번, 대기 중인 다른 요청=1건
설명: 2번 좌석 학생이 손을 들고 질문을 기다리고 있습니다. 대기 중인 요청이 1건 더 있습니다.

상황: 종류=자리 비움, 좌석=4번, 대기 중인 다른 요청=0건
설명: 4번 좌석 학생이 자리를 비웠습니다."""


class LLMError(Exception):
    """이 단계 실패 → 다음 단계로."""


# Gemini 가 실패하면 잠시 건너뛴다 (죽어 있는 동안 사건마다 5초씩 버리지 않게).
# 여러 설명 스레드가 같이 보므로 lock 으로 감싼다.
_skip_lock = threading.Lock()
_skip_until: dict[str, float] = {}          # mode -> 이 시각까지 건너뜀 (time.monotonic)


def _skipping(mode: str) -> float:
    """건너뛰는 중이면 남은 초, 아니면 0."""
    with _skip_lock:
        return max(0.0, _skip_until.get(mode, 0.0) - time.monotonic())


def _mark_failed(mode: str, cooldown_s: float):
    if cooldown_s > 0:
        with _skip_lock:
            _skip_until[mode] = time.monotonic() + cooldown_s


def _mark_ok(mode: str):
    with _skip_lock:
        _skip_until.pop(mode, None)


# ---------------------------------------------------------------- 프롬프트 / 기본 문구

def build_prompt(req: dict) -> str:
    kind = KIND_KO.get(req.get("kind"), str(req.get("kind")))
    waiting = int(req.get("waiting_count") or 0)
    return f"상황: 종류={kind}, 좌석={req.get('seat')}번, 대기 중인 다른 요청={waiting}건\n설명:"


def default_text(req: dict) -> str:
    """SD-08 No.8 build_default_text. 예: "3번 좌석 쓰러짐 감지" """
    kind = DEFAULT_KIND_KO.get(req.get("kind"), str(req.get("kind")))
    return f"{req.get('seat')}번 좌석 {kind} 감지"


_HANZI = re.compile(r"[一-鿿]")          # 중국어 한자 (qwen 이 가끔 섞음)
_QUOTES = "\"'“”‘’「」『』`"
_SENTENCE_END = re.compile(r"(다|요|니다|세요)[.!]?$|[.!?]$")   # 완성된 문장 끝 (잘린 답 거르기)


def clean_text(text: str, max_chars: int = 120) -> str:
    """LLM 답을 화면용 한 줄로 다듬는다. 쓸 수 없으면 LLMError."""
    text = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    text = text.strip(_QUOTES + " ")
    text = re.sub(r"^(설명|답변|답)\s*[:：]\s*", "", text)        # "설명:" 머리말 제거
    text = text.strip(_QUOTES + " ")                               # 머리말 뒤의 따옴표까지
    if not text:
        raise LLMError("빈 답")
    if _HANZI.search(text):
        raise LLMError(f"한자가 섞인 답: {text[:40]}")
    if not re.search(r"[가-힣]", text):
        raise LLMError(f"한국어가 아닌 답: {text[:40]}")
    if len(text) < 10 or not _SENTENCE_END.search(text):
        raise LLMError(f"끝나지 않은 문장: {text[:40]}")
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------- HTTP

def _post_json(url: str, body: dict, timeout: float, headers: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        try:
            detail = json.loads(detail)["error"]["message"][:200]
        except (ValueError, KeyError, TypeError):
            pass
        raise LLMError(f"HTTP {e.code}: {detail}") from None
    except (TimeoutError, urllib.error.URLError, OSError) as e:      # 타임아웃·연결 거부·DNS
        reason = getattr(e, "reason", e)
        raise LLMError(f"연결 실패/타임아웃: {reason}") from None
    except ValueError as e:
        raise LLMError(f"JSON 아님: {e}") from None


# ---------------------------------------------------------------- 1차 Gemini

def ask_gemini(req: dict, cfg: dict) -> str:
    key = cfg.get("gemini_api_key")
    if not key:
        raise LLMError("GEMINI_API_KEY 없음")
    gen_cfg = {"temperature": float(cfg.get("temperature", 0.3)),
               "maxOutputTokens": int(cfg.get("gemini_max_tokens", 1024))}   # 생각하는 모델은 생각도 여기서 씀
    if cfg.get("gemini_thinking_level"):
        gen_cfg["thinkingConfig"] = {"thinkingLevel": cfg["gemini_thinking_level"]}
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": build_prompt(req)}]}],
        "generationConfig": gen_cfg,
    }
    url = cfg.get("gemini_url", GEMINI_URL).format(model=cfg["gemini_model"])
    data = _post_json(url, body, float(cfg.get("gemini_timeout_s", 5)),
                      headers={"x-goog-api-key": key})          # 키는 URL 이 아니라 헤더로 (로그에 안 남게)
    try:
        cand = data["candidates"][0]
        parts = cand["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or str(data)[:120]
        raise LLMError(f"답 없음: {reason}") from None
    finish = cand.get("finishReason", "STOP")
    if finish != "STOP":                       # MAX_TOKENS(길이 제한에 잘림), SAFETY 등 → 쓰지 않는다
        raise LLMError(f"답이 끝까지 안 옴 (finishReason={finish})")
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))   # 생각(thought) 부분은 뺀다
    return clean_text(text, int(cfg.get("max_chars", 120)))


# ---------------------------------------------------------------- 2차 Ollama

def ask_ollama(req: dict, cfg: dict) -> str:
    body = {
        "model": cfg["ollama_model"],
        "system": SYSTEM_PROMPT,
        "prompt": build_prompt(req),
        "stream": False,
        "keep_alive": cfg.get("ollama_keep_alive", "30m"),
        "options": {"temperature": float(cfg.get("temperature", 0.3)), "num_predict": 100},
    }
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"
    data = _post_json(url, body, float(cfg.get("ollama_timeout_s", 10)))
    if "error" in data:
        raise LLMError(f"ollama: {data['error']}")
    return clean_text(data.get("response", ""), int(cfg.get("max_chars", 120)))


def warmup(cfg: dict) -> bool:
    """Ollama 모델을 미리 GPU 에 올려 둔다 (첫 설명이 느리지 않게). 실패해도 괜찮다."""
    if not cfg.get("ollama_enabled", True):
        return False
    url = cfg.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/generate"
    t0 = time.time()
    try:
        _post_json(url, {"model": cfg["ollama_model"], "prompt": "",
                         "keep_alive": cfg.get("ollama_keep_alive", "30m")}, timeout=60)
        log.info("ollama %s 준비 완료 (%.1fs)", cfg["ollama_model"], time.time() - t0)
        return True
    except LLMError as e:
        log.warning("ollama 준비 실패 (설명 요청 때 기본 문구로 대체될 수 있음): %s", e)
        return False


# ---------------------------------------------------------------- 순서대로

def explain_event(req: dict, cfg: dict, only: str | None = None) -> tuple[str, str]:
    """(text, mode). 절대 예외를 던지지 않는다. only 는 시험용 ("gemini"/"ollama"/"default")."""
    steps = [("gemini", ask_gemini, cfg.get("gemini_enabled", True), cfg.get("gemini_cooldown_s", 60)),
             ("ollama", ask_ollama, cfg.get("ollama_enabled", True), 0)]
    for mode, fn, enabled, cooldown in steps:
        if not enabled or (only and only != mode):
            continue
        name = f"{mode}({cfg.get(mode + '_model')})"         # 로그에 모델 이름까지
        left = _skipping(mode) if not only else 0.0      # --only 시험 때는 항상 시도
        if left:
            log.info("%s 건너뜀 (최근 실패, %.0f초 뒤 다시 시도) event=%s", name, left, req.get("event_id"))
            continue
        t0 = time.time()
        try:
            text = fn(req, cfg)
            _mark_ok(mode)
            log.info("설명 %s (%.2fs) event=%s: %s", name, time.time() - t0, req.get("event_id"), text)
            return text, mode
        except LLMError as e:
            _mark_failed(mode, float(cooldown))
            log.warning("%s 실패 (%.2fs) event=%s: %s", name, time.time() - t0, req.get("event_id"), e)
        except Exception:                                   # 예상 못 한 오류도 다음 단계로
            _mark_failed(mode, float(cooldown))
            log.exception("%s 처리 중 오류 event=%s", name, req.get("event_id"))
    text = default_text(req)
    log.info("설명 default event=%s: %s", req.get("event_id"), text)
    return text, "default"


def main():
    from assistant_config import load_assistant_config

    ap = argparse.ArgumentParser(description="AI 조교 상황 설명 단독 시험")
    ap.add_argument("--kind", default="fall", choices=sorted(KIND_KO))
    ap.add_argument("--seat", type=int, default=3)
    ap.add_argument("--waiting", type=int, default=0)
    ap.add_argument("--only", choices=["gemini", "ollama", "default"], help="이 단계만 시험")
    ap.add_argument("--timeout", type=float, help="이번 시험에서만 Gemini 대기 초 (설정 파일은 안 바뀜)")
    ap.add_argument("--model", help="이번 시험에서만 쓸 Gemini 모델")
    ap.add_argument("--repeat", type=int, default=1, help="같은 요청을 N번 보내고 성공률·시간 요약")
    ap.add_argument("--gap", type=float, default=2.0, help="--repeat 사이 쉬는 초 (기본 2)")
    ap.add_argument("--warmup", action="store_true", help="Ollama 모델만 미리 올려 보기")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    cfg = load_assistant_config()
    if args.timeout:
        cfg["gemini_timeout_s"] = args.timeout
    if args.model:
        cfg["gemini_model"] = args.model
    if args.warmup:
        warmup(cfg)
        return
    if args.repeat > 1:
        cfg["gemini_cooldown_s"] = 0            # 반복 시험에서는 매번 Gemini 를 시도
    print(f"Gemini {cfg['gemini_model']} 대기 {cfg['gemini_timeout_s']}초"
          f"{' / ' + str(args.repeat) + '회' if args.repeat > 1 else ''}\n")

    req = {"event_id": 0, "kind": args.kind, "seat": args.seat,
           "elapsed_s": 0.0, "waiting_count": args.waiting}
    results = []                                 # (mode, 걸린 초)
    for i in range(args.repeat):
        if i:
            time.sleep(args.gap)
        req["event_id"] = i
        t0 = time.time()
        text, mode = explain_event(req, cfg, only=args.only)
        took = time.time() - t0
        results.append((mode, took))
        print(f"[{mode}] {text}   ({took:.2f}s)")

    if args.repeat > 1:
        print("\n--- 요약 ---")
        for mode in ("gemini", "ollama", "default"):
            ts = sorted(t for m, t in results if m == mode)
            if ts:
                print(f"{mode:8} {len(ts):2}/{len(results)}회  평균 {sum(ts) / len(ts):.2f}s"
                      f"  최소 {ts[0]:.2f}s  최대 {ts[-1]:.2f}s")


if __name__ == "__main__":
    main()
