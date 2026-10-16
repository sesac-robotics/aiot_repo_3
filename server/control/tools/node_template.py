"""
노드 클라이언트 템플릿. 새 노드를 만들 때 복사해서 TODO 부분을 채운다.

    cp tools/node_template.py mynode.py
    python3 mynode.py [--server 127.0.0.1]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# 어느 폴더로 복사해도 common/ 을 찾을 수 있게 한다
_here = Path(__file__).resolve().parent
for _p in (_here, *_here.parents):
    if (_p / "common" / "client.py").exists():
        sys.path.insert(0, str(_p))
        break

from common.client import NodeClient           # noqa: E402
from common.config import load_config          # noqa: E402

# ---- TODO: 노드마다 고친다 ----------------------------------------------------
NODE = "student1"        # fixedcam / tracker / student1~4
CAMERA = {"index": 0, "width": 640, "height": 480, "fps": 15}
SEATS = [1]              # 1~4 좌석, 5 = 매트
LOOP_FPS = 15
# ---------------------------------------------------------------------------


def on_message(msg: dict):
    """hub 가 보낸 메시지 (수신 스레드에서 불림)."""
    if msg["type"] != "ack":
        print(f"<- hub: {msg['type']} {msg['value']}")


def detect() -> dict:
    """TODO: 카메라 프레임을 보고 현재 상태를 돌려준다."""
    return {"hand": False}


def main():
    ap = argparse.ArgumentParser(description=f"{NODE} 노드")
    ap.add_argument("--server", default=None, help="hub IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(hub_host=args.server, hub_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    client = NodeClient(NODE, cfg["hub_host"], int(cfg["hub_port"]), on_message=on_message)
    client.set_info({"camera": CAMERA,
                     "seats": [{"seat": s, "label": f"{s}번"} for s in SEATS]})
    client.start()
    print(f"{NODE}: hub {cfg['hub_host']}:{cfg['hub_port']} 에 접속 중...")

    connected_once = False
    prev = detect()
    frames, t0 = 0, time.time()
    try:
        while True:
            start = time.time()
            if not connected_once and client.connected:
                print(f"{NODE}: 연결됨. 대기 중... (Ctrl+C 로 끝)")
                connected_once = True

            state = detect()
            # 사건은 상태가 바뀐 순간에만 보낸다
            if state["hand"] != prev["hand"]:
                client.send("hand_raise", seat=SEATS[0],
                            value={"state": "start" if state["hand"] else "end"})
            prev = state

            frames += 1
            if time.time() - t0 >= 1.0:
                client.set_fps(frames / (time.time() - t0))
                frames, t0 = 0, time.time()
            time.sleep(max(0.0, 1.0 / LOOP_FPS - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    main()
