"""
가짜 노드: control 과의 TCP 통신, 이벤트 관리, DB 기록을 시험한다 (상세 설계 9장 "통신").

모드 (--node 또는 아래 NODE 값):
- scenario : vision_service · instructor_console · student_client_1~4 를
             한 프로세스에서 모두 띄워 아래 흐름을 돌린다. (기본)
             팬틸트는 TCP 노드가 아니라 hub 안의 모듈이라 여기서 흉내 내지 않는다 (hub --pantilt-sim 으로 확인).
               1) 2번 좌석 질문 start      -> event_update, queue_state, track_target
               2) 3번 좌석 쓰러짐 start    -> 선점 (쓰러짐이 active, 질문은 waiting)
               3) 강사 confirm(쓰러짐) -> resolve(쓰러짐) -> 질문이 다시 active
               4) 강사 confirm(질문) -> resolve(질문) -> 대기열 비움, track_target 없음
             --fall-wait 를 주면 2)에서 그 초만큼 기다려 15초/30초 fall_alert 도 볼 수 있다.
- vision_service / instructor_console / student_client_N :
             그 이름으로 접속해서 받은 메시지를 출력한다. vision_service 는 2번 좌석 질문을 한 번 보낸다.
             Ctrl+C 로 끝.

접속 후 2초마다 heartbeat 는 NodeClient 가 알아서 보낸다 (node_status 표).

실행 (service/jarvis_control_service 폴더에서):
    python3 -m tools.fake_node                       # scenario
    python3 -m tools.fake_node --node instructor_console --server 127.0.0.1
VS Code Run Code(▶)로는 이 파일을 직접 실행하고, 모드는 아래 NODE 로 고른다.
"""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

if __package__ in (None, ""):                 # tools/fake_node.py 를 직접 실행한 경우
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))   # aiot_repo_3/ (common/)

from common.config import load_config          # noqa: E402
from common.node_client import NodeClient      # noqa: E402
from common.protocol import (CONSOLE_NODE, STUDENT_NODES,  # noqa: E402
                             VISION_NODE)

# ---- Run Code 로 실행할 때 모드 (옵션 --node 가 있으면 그게 우선) -----------
NODE = "scenario"        # scenario / vision_service / instructor_console / student_client_1~4
# ---------------------------------------------------------------------------

ALL_NODES = [VISION_NODE, CONSOLE_NODE, *STUDENT_NODES]
SHOW = {"event_update", "queue_state", "device_update", "snapshot", "class_condition",
        "track_target", "explain_request", "pan_tilt", "fall_alert", "fall_alert_cancel"}


class FakeNode:
    """접속해서 받은 메시지를 출력하고 모아 둔다. 노드별로 흉내 낼 응답도 한다."""

    def __init__(self, node: str, cfg: dict, quiet_nudge: bool = True):
        self.node = node
        self.received: list[dict] = []
        self.cond = threading.Condition()
        self.client = NodeClient(node, cfg["control_host"], int(cfg["control_port"]),
                                 on_message=self.on_message)
        self.quiet_nudge = quiet_nudge

    def start(self, timeout_s: float = 3.0) -> bool:
        if self.node == VISION_NODE:
            self.client.set_status(cam1_fps=15.0, cam2_fps=15.0)
        self.client.start()
        deadline = time.time() + timeout_s
        while not self.client.connected and time.time() < deadline:
            time.sleep(0.05)
        return self.client.connected

    def stop(self):
        self.client.stop()

    def on_message(self, msg: dict):
        t, v = msg["type"], msg["value"]
        with self.cond:
            self.received.append(msg)
            self.cond.notify_all()
        if t in SHOW and not (self.quiet_nudge and t == "pan_tilt" and v.get("mode") == "nudge"):
            print(f"  [{self.node:18s}] <- {t} {_short(t, v)}")
        # ---- 노드별 흉내 ----
        if self.node == VISION_NODE and t == "explain_request":
            self.client.send("explain_text", value={
                "event_id": v["event_id"], "mode": "default",
                "text": f"{v['seat']}번 좌석 {v['kind']} (가짜 설명)"})

    def wait_for(self, type_: str, pred=lambda v: True, timeout_s: float = 3.0):
        """type_ 이고 pred(value) 가 참인 메시지가 올 때까지 기다린다."""
        deadline = time.time() + timeout_s
        with self.cond:
            while True:
                for m in self.received:
                    if m["type"] == type_ and pred(m["value"]):
                        return m["value"]
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(f"{self.node}: {type_} 를 받지 못함")
                self.cond.wait(left)


def _short(t: str, v: dict) -> dict:
    if t == "event_update":
        keys = ("event_id", "seat", "kind", "status", "alert_stage", "confirmed_by",
                "close_reason", "explain_text")
        return {k: v.get(k) for k in keys if v.get(k) is not None}
    if t == "snapshot":
        return {"events": len(v["events"]), "queue": v["queue"], "devices": len(v["devices"])}
    return v


def run_scenario(cfg: dict, fall_wait_s: float):
    nodes = {n: FakeNode(n, cfg) for n in ALL_NODES}
    try:
        for n, fn in nodes.items():
            if not fn.start():
                print(f"!! {n}: control({cfg['control_host']}:{cfg['control_port']}) "
                      f"접속 실패 - run_hub.py 가 켜져 있나요?")
                return False
        vision, console = nodes[VISION_NODE], nodes[CONSOLE_NODE]
        time.sleep(0.5)

        def ev_of(seat, kind):
            return console.wait_for("event_update",
                                    lambda v: v["seat"] == seat and v["kind"] == kind)["event_id"]

        print("\n1) 2번 좌석 질문 start")
        vision.client.send("question", seat=2, value={"state": "start"}, conf=0.9)
        q_id = ev_of(2, "question")
        console.wait_for("queue_state", lambda v: v["active"] == q_id)
        vision.wait_for("track_target", lambda v: v["seat"] == 2)
        vision.client.send("track_offset", seat=2, value={"found": True, "dx": 0.1, "dy": -0.05})

        print("\n2) 3번 좌석 쓰러짐 start -> 선점")
        vision.client.send("fall", seat=3, value={"state": "start"}, conf=0.95)
        f_id = ev_of(3, "fall")
        console.wait_for("queue_state", lambda v: v["active"] == f_id and q_id in v["waiting"])
        vision.wait_for("track_target", lambda v: v["seat"] == 3)
        if fall_wait_s > 0:
            print(f"   ... {fall_wait_s:.0f}초 기다리며 단계 알림 확인")
            nodes["student_client_4"].wait_for("fall_alert", lambda v: v["stage"] == "neighbor",
                                               timeout_s=fall_wait_s)        # 3번의 옆자리 (2×2: 1, 4)
            if fall_wait_s >= 30:
                nodes["student_client_2"].wait_for("fall_alert", lambda v: v["stage"] == "all",
                                                   timeout_s=fall_wait_s)

        print("\n3) 강사 confirm -> resolve (쓰러짐)")
        console.client.send("confirm", value={"event_id": f_id})
        console.wait_for("event_update", lambda v: v["event_id"] == f_id and v["confirmed_at"])
        console.client.send("resolve", value={"event_id": f_id})
        console.wait_for("event_update", lambda v: v["event_id"] == f_id and v["status"] == "closed")
        console.wait_for("queue_state", lambda v: v["active"] == q_id and not v["waiting"])

        print("\n4) 강사 confirm -> resolve (질문)")
        console.client.send("confirm", value={"event_id": q_id})
        console.client.send("resolve", value={"event_id": q_id})
        console.wait_for("queue_state", lambda v: v["active"] is None and not v["waiting"])
        vision.wait_for("track_target", lambda v: v["seat"] is None)
        print("\n성공: 질문 1건 + 쓰러짐 선점이 끝까지 흘렀습니다. (DB: db/hub.db 의 help_event, event_log)")
        return True
    except TimeoutError as e:
        print(f"\n실패: {e}")
        return False
    finally:
        for fn in nodes.values():
            fn.stop()


def run_one(node: str, cfg: dict):
    fn = FakeNode(node, cfg)
    if not fn.start():
        print(f"!! {node}: control 접속 실패 - run_hub.py 가 켜져 있나요?")
        return
    print(f"-> {node}: 접속됨 (Ctrl+C 로 끝)")
    if node == VISION_NODE:
        fn.client.send("question", seat=2, value={"state": "start"}, conf=0.9)
    try:
        while True:
            seat = int(node.rsplit("_", 1)[1]) if node in STUDENT_NODES else None
            if seat:
                fn.client.send("condition_summary", value={"focus_low": False, "posture_bad": seat % 2 == 0})
            fn.client.set_fps(15.0)
            time.sleep(10 if seat else 1)
    finally:
        fn.stop()


def main():
    ap = argparse.ArgumentParser(description="control 시험용 가짜 노드")
    ap.add_argument("--node", default=NODE, choices=["scenario", *ALL_NODES])
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--fall-wait", type=float, default=0,
                    help="scenario 에서 쓰러짐 뒤 기다릴 초 (16 이상이면 옆자리 알림, 31 이상이면 전체 알림)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(control_host=args.server, control_port=args.port)
    try:
        if args.node == "scenario":
            sys.exit(0 if run_scenario(cfg, args.fall_wait) else 1)
        run_one(args.node, cfg)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
