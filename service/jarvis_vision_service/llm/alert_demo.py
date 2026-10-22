"""
알림 문구 터미널 시험 (기능 1: 이벤트 상황에 맞는 알림 문구).

control 에 TCP 로 두 노드를 붙인다.
  - vision_service     : 사건(start/end)을 보내고, explain_request 를 받으면 llm.py 로 문구를 만들어 explain_text 로 돌려준다
  - instructor_console : 강사 화면 대신. event_update 의 알림 문구를 터미널에 찍고, 확인·완료·오감지를 보낸다
그래서 "사건 → control(event_manager) → explain_request → LLM → explain_text → control → event_update" 한 바퀴를 본다.

실행 (control 을 먼저 켠다. run_assistant.py 는 끈다 — 같은 vision_service 이름이라 겹친다):
    터미널 1:  cd service/jarvis_control_service && python3 run_hub.py
    터미널 2:  cd service/jarvis_vision_service  && python3 llm/alert_demo.py            # 명령을 직접 입력
               python3 llm/alert_demo.py --auto                                         # 정해진 시나리오 (약 45초)
               python3 llm/alert_demo.py --alert-ollama                                 # 알림 문구에 Gemini 를 쓰지 않음 (한도 아끼기)

명령:
    f 3        3번 좌석 쓰러짐 start        f 3 end   일어남 (end)
    q 2        2번 좌석 질문(손 들기) start  q 2 end   손 내림
    a 4        4번 좌석 자리 비움 start      a 4 end   복귀 (이벤트 닫힘)
    c 1 / r 1 / d 1    이벤트 1 확인(confirm) / 완료(resolve) / 오감지(dismiss)
    ls         열린 이벤트 목록          auto   시나리오 실행          h   도움말          x   끝

시간으로 생기는 상황은 control.yaml 의 timers 를 따른다:
    쓰러짐 미확인 15초 → 옆자리 알림, 30초 → 전체 알림, 자리 비움 60초 → 미복귀
"""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent                         # service/jarvis_vision_service/llm/
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))                           # run_assistant.py 가 있는 vision_service 폴더
sys.path.insert(0, str(next(p for p in HERE.parents if (p / "common" / "protocol.py").exists())))   # aiot_repo_3/

import llm                                                     # noqa: E402
import run_assistant as ra                                     # noqa: E402  (LLM_CFG, explain_slots 재사용)
from common.config import load_config                          # noqa: E402
from common.node_client import NodeClient                      # noqa: E402
from common.protocol import CONSOLE_NODE, VISION_NODE          # noqa: E402

KIND = {"f": "fall", "q": "question", "a": "away"}
KIND_KO = {"fall": "쓰러짐", "question": "질문", "away": "자리 비움"}
SITUATION_KO = {"detect": "새로 감지", "again": "다시 감지", "fall_neighbor": "옆자리 알림",
                "fall_all": "전체 알림", "fall_risen": "일어남", "hand_down": "손 내림",
                "away_overdue": "미복귀"}
CLOSE_KO = {"resolved": "완료", "false_alarm": "오감지", "expired": "만료", "cleared": "복귀"}

print_lock = threading.Lock()
T0 = time.time()


def say(line: str = ""):
    with print_lock:
        print(f"{time.time() - T0:6.1f}s  {line}" if line else "", flush=True)


class Demo:
    def __init__(self, host: str, port: int):
        self.events: dict[int, dict] = {}        # event_id -> 최신 event_update
        self.timing: dict[tuple, tuple] = {}     # (event_id, seq) -> (mode, 걸린 초)
        self.explain_cfg = ra.LLM_CFG                 # --alert-ollama 면 Gemini 를 끈 사본 (한도 아끼기)
        self.vision = NodeClient(VISION_NODE, host, port, on_message=self.on_vision)
        self.console = NodeClient(CONSOLE_NODE, host, port, on_message=self.on_console)

    # ---- 접속 ----
    def start(self) -> bool:
        self.vision.set_status(cam1_fps=0.0, cam2_fps=0.0)
        self.vision.start()
        self.console.start()
        deadline = time.time() + 5
        while time.time() < deadline and not (self.vision.connected and self.console.connected):
            time.sleep(0.05)
        return self.vision.connected and self.console.connected

    def stop(self):
        self.vision.stop()
        self.console.stop()

    # ---- vision_service 역할 ----
    def on_vision(self, msg: dict):
        if msg["type"] == "explain_request":
            v = msg["value"]
            say(f"   … 문구 요청 #{v['event_id']} {SITUATION_KO.get(v.get('situation'), v.get('situation'))}"
                f" (seq {v.get('seq')})")
            threading.Thread(target=self.explain, args=(v,), daemon=True).start()

    def explain(self, req: dict):
        t0 = time.time()
        with ra.explain_slots:
            text, mode = llm.explain_event(req, self.explain_cfg)
        self.timing[(req["event_id"], req.get("seq"))] = (mode, time.time() - t0)
        value = {"event_id": req["event_id"], "text": text, "mode": mode}
        if "seq" in req:
            value["seq"], value["situation"] = req["seq"], req.get("situation")
        self.vision.send("explain_text", value=value)

    def incident(self, kind: str, seat: int, state: str):
        self.vision.send(kind, seat=seat, value={"state": state})
        say(f"-> {seat}번 {KIND_KO[kind]} {state}")

    # ---- instructor_console 역할 ----
    def on_console(self, msg: dict):
        t, v = msg["type"], msg["value"]
        if t == "snapshot":
            for ev in v.get("events", []):
                self.events[ev["event_id"]] = ev
            return
        if t != "event_update":
            return
        prev = self.events.get(v["event_id"], {})
        self.events[v["event_id"]] = v
        head = f"#{v['event_id']} {v['seat']}번 {KIND_KO.get(v['kind'], v['kind'])}"
        if v.get("status") != prev.get("status"):
            if v["status"] == "closed":
                say(f"   [상태] {head} 닫힘 ({CLOSE_KO.get(v.get('close_reason'), v.get('close_reason'))})")
                self.events.pop(v["event_id"], None)
                return
            say(f"   [상태] {head} {'추적 중(active)' if v['status'] == 'active' else '대기(waiting)'}")
        if v.get("confirmed_at") and not prev.get("confirmed_at"):
            say(f"   [상태] {head} 확인됨 ({v.get('confirmed_by')})")
        if v.get("explain_text") and (v.get("explain_text"), v.get("explain_seq")) != \
                (prev.get("explain_text"), prev.get("explain_seq")):
            mode, took = self.timing.get((v["event_id"], v.get("explain_seq")), (v.get("explain_mode"), None))
            took_s = f" {took:.1f}s" if took is not None else ""
            sit = SITUATION_KO.get(v.get("explain_situation"), v.get("explain_situation") or "")
            say(f"📢 [알림] {head} · {sit}  ({mode}{took_s})")
            say(f"          {v['explain_text']}")

    def command(self, type_: str, event_id: int):
        self.console.send(type_, value={"event_id": event_id})
        say(f"-> 강사 {dict(confirm='확인', resolve='완료', dismiss='오감지')[type_]} #{event_id}")

    def show(self):
        if not self.events:
            say("열린 이벤트 없음")
        for ev in sorted(self.events.values(), key=lambda e: e["event_id"]):
            flags = [f for f, on in (("확인됨", ev.get("confirmed_at")), ("손 내림", ev.get("hand_down")),
                                     ("일어남", ev.get("risen")), ("미복귀", ev.get("overdue"))) if on]
            say(f"#{ev['event_id']} {ev['seat']}번 {KIND_KO.get(ev['kind'])} {ev['status']} "
                f"알림단계={ev.get('alert_stage')} {' '.join(flags)}")
            if ev.get("explain_text"):
                say(f"    \"{ev['explain_text']}\"")

    # ---- 시나리오 ----
    def auto(self):
        steps = [
            (0, "질문 2번 → 새로 감지", lambda: self.incident("question", 2, "start")),
            (6, "쓰러짐 3번 → 질문을 미루고 전환(선점)", lambda: self.incident("fall", 3, "start")),
            (12, "질문 4번 → 대기열 2번째", lambda: self.incident("question", 4, "start")),
            (16, "2번 손 내림", lambda: self.incident("question", 2, "end")),
            (19, "3번 일어남 (확인 전)", lambda: self.incident("fall", 3, "end")),
            (20.5, "(기다림) 3번 쓰러짐 15초 미확인 → 옆자리 알림", lambda: None),
            (27, "2번 다시 손 듦", lambda: self.incident("question", 2, "start")),
            (35.5, "(기다림) 3번 쓰러짐 30초 미확인 → 전체 알림", lambda: None),
            (40, "강사: 쓰러짐 확인 + 완료", lambda: (self.command("confirm", self._id("fall", 3)),
                                                   self.command("resolve", self._id("fall", 3)))),
            (44, "강사: 남은 질문 완료", lambda: [self.command("resolve", e) for e in
                                               [self._id("question", 2), self._id("question", 4)] if e]),
        ]
        say("=== 시나리오 시작 (약 45초, 자리 비움 미복귀는 60초라 빠짐 — 직접 'a 4' 로 시험) ===")
        t0 = time.time()
        for at, title, fn in steps:
            time.sleep(max(0.0, at - (time.time() - t0)))
            say()
            say(f"▶ {title}")
            fn()
        time.sleep(8)                                    # 마지막 문구까지 기다림
        say("=== 시나리오 끝 ===")

    def _id(self, kind: str, seat: int):
        for ev in self.events.values():
            if ev["kind"] == kind and ev["seat"] == seat:
                return ev["event_id"]
        return None


def repl(demo: Demo):
    print(__doc__.split("명령:")[1].split("시간으로")[0])
    while True:
        try:
            line = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if not line:
            continue
        p = line.split()
        try:
            if p[0] in ("x", "q!", "exit", "quit"):
                return
            if p[0] in ("h", "help", "?"):
                print(__doc__.split("명령:")[1].split("시간으로")[0])
            elif p[0] == "ls":
                demo.show()
            elif p[0] == "auto":
                demo.auto()
            elif p[0] in KIND and len(p) >= 2:
                demo.incident(KIND[p[0]], int(p[1]), "end" if len(p) > 2 and p[2] == "end" else "start")
            elif p[0] in ("c", "r", "d") and len(p) == 2:
                demo.command({"c": "confirm", "r": "resolve", "d": "dismiss"}[p[0]], int(p[1]))
            else:
                print("모르는 명령. h 로 도움말")
        except ValueError:
            print("좌석·이벤트 번호는 숫자로. 예: f 3, c 1")


def main():
    ap = argparse.ArgumentParser(description="알림 문구 터미널 시험")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--auto", action="store_true", help="정해진 시나리오만 돌리고 끝")
    ap.add_argument("--alert-ollama", action="store_true", help="알림 문구는 Ollama 만 (Gemini 하루 한도 아끼기)")
    ap.add_argument("-v", "--verbose", action="store_true", help="LLM 로그(실패·건너뜀)도 보기")
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="          %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)
    if not args.verbose:
        logging.getLogger("llm").setLevel(logging.ERROR)

    demo = Demo(cfg["control_host"], int(cfg["control_port"]))
    if args.alert_ollama:
        demo.explain_cfg = dict(ra.LLM_CFG, gemini_enabled=False)
    print(f"control {cfg['control_host']}:{cfg['control_port']} 에 접속 중...")
    if not demo.start():
        print("접속 실패. control(run_hub.py)이 켜져 있는지, run_assistant.py 가 꺼져 있는지 확인하세요.")
        demo.stop()
        return
    gem = ra.LLM_CFG["gemini_model"] if ra.LLM_CFG.get("gemini_api_key") else "키 없음(건너뜀)"
    print(f"접속됨. AI 조교: gemini={gem}, ollama={ra.LLM_CFG['ollama_model']}")
    if ra.LLM_CFG.get("ollama_warmup", True):
        threading.Thread(target=llm.warmup, args=(ra.LLM_CFG,), daemon=True).start()
    try:
        if args.auto:
            demo.auto()
        else:
            repl(demo)
    finally:
        demo.stop()


if __name__ == "__main__":
    main()
