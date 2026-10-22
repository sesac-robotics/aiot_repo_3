"""
[질의] 강사 자연어 질의 터미널 시험 (기능 2: 자연어 질의 + LLM tool calling, 조회만).

control 에 TCP 로 두 노드를 붙인다 (alert_demo.py 와 같은 방식).
  - instructor_console : 질문(ask)을 보내고 답(ask_answer)과 근거 도구를 터미널에 찍는다
  - vision_service     : ask_request 를 받아 ask.py 로 LLM tool calling → 도구는 tool_call 로 control 이 실행
                         (사건 넣기 명령도 이 노드로 보낸다)
"질문 → control → vision LLM ⇄ control 도구(DB 조회) → 답 → control → 강사 화면" 한 바퀴를 본다.

실행 (control 을 먼저 켠다. run_assistant.py 는 끈다 — 같은 vision_service 이름이라 겹친다):
    터미널 1:  cd service/jarvis_control_service && python3 run_hub.py
    터미널 2:  cd service/jarvis_vision_service  && python3 llm/ask_demo.py              # 질문을 직접 입력
               python3 llm/ask_demo.py --seed --questions                              # 예시 데이터 + 질문 10개 측정
               python3 llm/ask_demo.py --seed --questions --alert-ollama                 # 예시 데이터의 알림 문구는 Ollama 로 (한도 아끼기)
               python3 llm/ask_demo.py --ask "오늘 쓰러짐 몇 번 있었어?"                   # 한 번만 묻고 끝

입력:
    그냥 문장       질문 (예: 오늘 쓰러짐 몇 번 있었어?)
    /seed           예시 이벤트 만들기 (질문·쓰러짐·자리 비움·오감지, 약 15초)
    /questions      준비된 질문 10개를 차례로 묻고 요약
    /report         [수업 종료 리포트] — 오늘 숫자 + AI 요약·살펴볼 점
    /f 3, /q 2 end, /a 4, /c 1, /r 1, /d 1, /ls     사건 넣기·확인·완료·오감지·목록 (alert_demo 와 같음)
    /h  도움말      /x  끝
"""

import argparse
import json
import logging
import threading
import time

import alert_demo
import ask
import llm
import report
import run_assistant as ra
from alert_demo import KIND, Demo, say
from common.config import load_config

QUESTIONS = [
    "지금 대기 중인 요청 있어?",
    "오늘 쓰러짐 몇 번 있었어?",
    "3번 좌석 오늘 기록 보여줘",
    "오늘 질문은 평균 몇 초 만에 확인했어?",
    "오감지로 처리된 건 몇 건이야?",
    "장비 다 연결돼 있어?",
    "최근 30분 반 집중도 어때?",
    "가장 최근에 끝난 이벤트는 어떻게 처리됐어?",
    "2번 좌석 학생 이름이 뭐야?",                # 이름은 답하지 않아야 함
    "1번 이벤트 완료 처리해 줘",                  # 조회만 — 강사 화면에서 누르라고 안내해야 함
]


class AskDemo(Demo):
    def __init__(self, host: str, port: int):
        super().__init__(host, port)
        self.asker = ask.AskWorker(self.vision, ra.LLM_CFG)
        self.reporter = report.ReportWorker(self.vision, ra.LLM_CFG)         # [리포트]
        self.reports: dict[int, dict] = {}
        self.ask_id = int(time.time()) % 100000 * 10      # 재실행해도 번호가 겹치지 않게
        self.answers: dict[int, dict] = {}
        self.answer_cv = threading.Condition()

    # vision_service: ask_request / tool_result 는 AskWorker 로, 나머지(explain_request)는 alert_demo 그대로
    def on_vision(self, msg: dict):
        if not self.asker.on_message(msg) and not self.reporter.on_message(msg):
            super().on_vision(msg)

    # instructor_console: ask_answer 를 받아 둔다
    def on_console(self, msg: dict):
        if msg["type"] == "report":                                         # [리포트]
            with self.answer_cv:
                self.reports[msg["value"]["report_id"]] = msg["value"]
                self.answer_cv.notify_all()
            return
        if msg["type"] == "ask_answer":
            with self.answer_cv:
                self.answers[msg["value"]["ask_id"]] = msg["value"]
                self.answer_cv.notify_all()
            return
        super().on_console(msg)

    def ask(self, text: str, timeout_s: float = 45.0) -> dict | None:
        self.ask_id += 1
        aid = self.ask_id
        say()
        say(f"❓ {text}")
        t0 = time.time()
        if not self.console.send("ask", value={"ask_id": aid, "text": text}):
            say("   보내지 못함 (control 연결 확인)")
            return None
        with self.answer_cv:
            if not self.answer_cv.wait_for(lambda: aid in self.answers, timeout_s):
                say(f"   {timeout_s:.0f}초 안에 답이 오지 않음")
                return None
            v = self.answers.pop(aid)
        for reason in v.get("fallback") or []:
            say(f"   ⚠ {reason}")
        for t in v.get("tools") or []:
            say(f"   🔧 {t['name']} {json.dumps(t.get('args') or {}, ensure_ascii=False)} → {t.get('summary')}")
        say(f"💬 [{v.get('mode')} {time.time() - t0:.1f}s] " + v.get("text", "").replace("\n", "\n          "))
        return {**v, "round_trip_s": time.time() - t0}

    def report(self, timeout_s: float = 60.0):
        """[리포트] [수업 종료 리포트] 버튼과 같다. 숫자가 먼저 오고(writing), 문장이 나중에 온다(done)."""
        self.ask_id += 1
        rid = self.ask_id
        say()
        say("📄 [수업 종료 리포트]")
        t0 = time.time()
        if not self.console.send("report_request", value={"report_id": rid}):
            say("   보내지 못함 (control 연결 확인)")
            return None
        with self.answer_cv:
            ok = self.answer_cv.wait_for(lambda: self.reports.get(rid, {}).get("status") in ("done", "error"), timeout_s)
            r = self.reports.get(rid)
        if not r:
            say("   리포트가 오지 않음")
            return None
        d = r.get("data") or {}
        k = d.get("by_kind") or {}
        say(f"   숫자: {d.get('date')} 도움 요청 {d.get('total')}건 (쓰러짐 {k.get('fall')} · 질문 {k.get('question')} · "
            f"자리비움 {k.get('away')}) · 평균 확인 {d.get('avg_confirm')} · 최대 {d.get('max_confirm')}")
        for s_, v in (d.get("by_seat") or {}).items():
            say(f"   {s_}번 좌석 {v['total']}건")
        for o in d.get("device_outages") or []:
            say(f"   장비 끊김 {o['name']} {o['from']}~{o['to']}")
        if not ok:
            say(f"   {timeout_s:.0f}초 안에 문장이 오지 않음")
            return r
        for reason in r.get("fallback") or []:
            say(f"   ⚠ {reason}")
        say(f"💬 [{r.get('mode')} {time.time() - t0:.1f}s] 요약: {r.get('summary')}")
        for i, sug in enumerate(r.get("suggestions") or [], 1):
            say(f"          살펴볼 점 {i}. {sug}")
        return r

    def seed(self):
        """예시 이벤트. 실제 LLM 알림 문구도 같이 만들어진다."""
        say("=== 예시 이벤트 만드는 중 (약 15초) ===")
        steps = [
            lambda: self.incident("question", 2, "start"),
            lambda: self.incident("fall", 3, "start"),
            lambda: self.command("confirm", self._id("fall", 3)),
            lambda: self.command("resolve", self._id("fall", 3)),
            lambda: self.incident("away", 4, "start"),
            lambda: self.incident("question", 1, "start"),
            lambda: self.command("dismiss", self._id("question", 1)),
            lambda: self.incident("away", 4, "end"),
            lambda: self.command("confirm", self._id("question", 2)),
        ]
        for fn in steps:
            fn()
            time.sleep(1.5)
        time.sleep(3)
        say("=== 예시 이벤트 끝: 2번 질문은 열려 있음 ===")

    def run_questions(self, questions=QUESTIONS):
        results = []
        for q in questions:
            r = self.ask(q)
            results.append((q, r))
        say()
        say("--- 요약 ---")
        modes = {}
        for q, r in results:
            mode = r.get("mode") if r else "응답 없음"
            modes[mode] = modes.get(mode, 0) + 1
            took = f"{r['round_trip_s']:.1f}s" if r else "-"
            tools = ",".join(t["name"] for t in (r or {}).get("tools") or []) or "-"
            say(f"[{mode:7}] {took:>6}  도구 {tools:40.40}  {q}")
        say("모드별: " + ", ".join(f"{k} {v}회" for k, v in modes.items()))
        reasons = sorted({x for _, r in results if r for x in r.get("fallback") or []})
        if reasons:
            say("앞 단계를 못 쓴 이유: " + " / ".join(reasons)[:300])


def repl(demo: AskDemo):
    print(__doc__.split("입력:")[1])
    while True:
        try:
            line = input("? ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not line:
            continue
        if not line.startswith("/"):
            demo.ask(line)
            continue
        p = line[1:].lower().split()
        try:
            if not p or p[0] in ("x", "exit", "quit"):
                return
            if p[0] in ("h", "help"):
                print(__doc__.split("입력:")[1])
            elif p[0] == "seed":
                demo.seed()
            elif p[0] == "questions":
                demo.run_questions()
            elif p[0] == "report":
                demo.report()
            elif p[0] == "ls":
                demo.show()
            elif p[0] in KIND and len(p) >= 2:
                demo.incident(KIND[p[0]], int(p[1]), "end" if len(p) > 2 and p[2] == "end" else "start")
            elif p[0] in ("c", "r", "d") and len(p) == 2:
                demo.command({"c": "confirm", "r": "resolve", "d": "dismiss"}[p[0]], int(p[1]))
            else:
                print("모르는 명령. /h 로 도움말")
        except ValueError:
            print("좌석·이벤트 번호는 숫자로. 예: /f 3, /c 1")


def main():
    ap = argparse.ArgumentParser(description="강사 자연어 질의 터미널 시험")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--ask", help="이 질문 하나만 묻고 끝")
    ap.add_argument("--seed", action="store_true", help="시작할 때 예시 이벤트 만들기")
    ap.add_argument("--questions", action="store_true", help="준비된 질문 10개를 묻고 요약 후 끝")
    ap.add_argument("--report", action="store_true", help="[수업 종료 리포트] 를 한 번 만들고 끝")
    ap.add_argument("--alert-ollama", action="store_true",
                    help="--seed 의 알림 문구는 Ollama 만 (Gemini 하루 한도를 질의에 아끼기)")
    ap.add_argument("-v", "--verbose", action="store_true", help="LLM·도구 로그도 보기")
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="          %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)
    if not args.verbose:
        logging.getLogger("llm").setLevel(logging.ERROR)
        logging.getLogger("ask").setLevel(logging.ERROR)

    demo = AskDemo(cfg["control_host"], int(cfg["control_port"]))
    if args.alert_ollama:
        demo.explain_cfg = dict(ra.LLM_CFG, gemini_enabled=False)
    print(f"control {cfg['control_host']}:{cfg['control_port']} 에 접속 중...")
    if not demo.start():
        print("접속 실패. control(run_hub.py)이 켜져 있는지, run_assistant.py 가 꺼져 있는지 확인하세요.")
        demo.stop()
        return
    gem = ask._gemini_model(ra.LLM_CFG) if ra.LLM_CFG.get("gemini_api_key") else "키 없음(건너뜀)"
    olm = ask._ollama_model(ra.LLM_CFG)
    print(f"접속됨. 질의 LLM: gemini={gem}, ollama={olm}")
    if ra.LLM_CFG.get("ollama_warmup", True):
        threading.Thread(target=llm.warmup, args=(ra.LLM_CFG,), daemon=True).start()
    try:
        if args.seed:
            demo.seed()
        if args.report:
            demo.report()
        elif args.ask:
            demo.ask(args.ask)
        elif args.questions:
            demo.run_questions()
        else:
            repl(demo)
    finally:
        demo.stop()


if __name__ == "__main__":
    main()
