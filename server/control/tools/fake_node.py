"""
가짜 노드: hub 와의 TCP 통신과 DB 기록을 시험한다.

모드 (--node 또는 아래 NODE 값):
- fixedcam / tracker / student1~4 : 그 이름으로 접속해서
      node_info(카메라·좌석) -> 사건 하나(hand_raise) -> 이후 1초마다 presence
      hub 가 보내는 ack 를 출력한다. Ctrl+C 로 끝.
- all : 6개 노드로 차례로 접속해 node_info 만 보내고 끝낸다 (cameras / seats 표 채우기)

접속 후 2초마다 heartbeat 는 NodeClient 가 알아서 보낸다 (node_status 표).
결과 확인:  python3 -m tools.show_db

실행 (src 폴더에서):
    python3 -m tools.fake_node --node all
    python3 -m tools.fake_node --node student3 --server 127.0.0.1
VS Code Run Code(▶)로는 src 맨 위의 run_client.py 를 실행하고, 모드는 아래 NODE 로 고른다.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

if __package__ in (None, ""):                 # tools/fake_node.py 를 직접 실행한 경우
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.client import NodeClient           # noqa: E402
from common.config import load_config          # noqa: E402

# ---- Run Code 로 실행할 때 모드 (옵션 --node 가 있으면 그게 우선) -----------
NODE = "all"             # all / fixedcam / tracker / student1~4
# ---------------------------------------------------------------------------

SEAT_LABELS = {1: "1번 좌석", 2: "2번 좌석", 3: "3번 좌석", 4: "4번 좌석", 5: "매트"}

# 노드별 가짜 카메라·좌석 정보 (index, width, height, fps, 좌석들)
FAKE_NODES = {
    "fixedcam": (0, 1280, 720, 15, [1, 2, 3, 4, 5]),
    "tracker":  (1, 640, 480, 15, [1, 2, 3, 4, 5]),
    "student1": (0, 640, 480, 15, [1]),
    "student2": (0, 640, 480, 15, [2]),
    "student3": (0, 640, 480, 15, [3]),
    "student4": (0, 640, 480, 15, [4]),
}


def fake_info(node: str) -> dict:
    index, width, height, fps, seats = FAKE_NODES[node]
    return {"camera": {"index": index, "width": width, "height": height, "fps": fps},
            "seats": [{"seat": s, "label": SEAT_LABELS[s]} for s in seats]}


def connect(node: str, cfg: dict, on_message=None, timeout_s: float = 3.0):
    """접속하고 node_info 를 보낸다. 접속 못 하면 None."""
    client = NodeClient(node, cfg["hub_host"], int(cfg["hub_port"]), on_message=on_message)
    client.set_info(fake_info(node))           # 접속하자마자 자동 전송
    client.start()
    deadline = time.time() + timeout_s
    while not client.connected and time.time() < deadline:
        time.sleep(0.1)
    if not client.connected:
        client.stop()
        print(f"!! {node}: hub({cfg['hub_host']}:{cfg['hub_port']}) 접속 실패 - hub 가 켜져 있나요?")
        return None
    return client


def run_all(cfg: dict):
    for node in FAKE_NODES:
        client = connect(node, cfg)
        if client is None:
            return
        time.sleep(0.3)                        # node_info 가 나갈 시간
        client.stop()
        info = fake_info(node)
        print(f"-> {node:9s} 카메라={info['camera']} 좌석={[s['seat'] for s in info['seats']]}")
    print("끝. 확인: python3 tools/show_db.py")


def run_one(node: str, cfg: dict):
    client = connect(node, cfg, on_message=lambda m: print(f"  <- hub: {m['type']} {m['value']}"))
    if client is None:
        return
    seat = FAKE_NODES[node][4][0]
    print(f"-> {node}: node_info 보냄, hand_raise seat {seat}")
    client.send("hand_raise", seat=seat, value={"state": "start"}, conf=0.9)
    try:
        while True:                            # 1초마다 재실 정보 (Ctrl+C 로 끝)
            client.send("presence", value={"seats": [1, 1, 0, 1]})
            client.set_fps(15.0)
            time.sleep(1)
    finally:
        client.stop()


def main():
    ap = argparse.ArgumentParser(description="hub 시험용 가짜 노드")
    ap.add_argument("--node", default=NODE, choices=["all", *FAKE_NODES])
    ap.add_argument("--server", default=None, help="hub IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(hub_host=args.server, hub_port=args.port)
    try:
        if args.node == "all":
            run_all(cfg)
        else:
            run_one(args.node, cfg)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
