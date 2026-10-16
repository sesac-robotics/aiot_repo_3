"""
노드 클라이언트 템플릿 - 새 노드를 만들 때 이 파일을 복사해서 시작한다.

    cp tools/node_template.py mynode.py     # 어디에 복사해도 common/ 을 알아서 찾는다

그대로 실행하면 hub 와의 최소 연결을 확인한다:
  1. hub 에 접속하고 node_info(PC·카메라·좌석)를 보낸다   -> DB nodes / cameras / seats
  2. 처음 연결됐을 때 한 번만 "연결됨" 을 출력하고, 이후로는 조용히 대기한다
  3. 대기하는 동안 2초마다 하트비트가 자동으로 나간다     -> DB node_status
     (끊기면 백그라운드에서 알아서 재접속한다. 그 과정은 출력하지 않는다)
  Ctrl+C 로 끝.

FAKE_EVENTS = True 로 바꾸면 5초마다 가짜 손들기를 보내 ack 왕복도 확인할 수 있다.
QUIET = False 로 바꾸면 접속·끊김·재접속 로그가 전부 보인다 (문제 찾을 때).

실행:  python3 tools/node_template.py [--server 127.0.0.1]
고칠 곳은 "TODO" 로 표시해 두었다. 소켓은 직접 열지 말고 client.send(...) 만 쓴다.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# 이 파일을 어디로 복사해도 common/ 이 있는 폴더를 찾아 import 경로에 넣는다
_here = Path(__file__).resolve().parent
for _p in (_here, *_here.parents):
    if (_p / "common" / "client.py").exists():
        sys.path.insert(0, str(_p))
        break

from common.client import NodeClient           # noqa: E402
from common.config import load_config          # noqa: E402

# ---- TODO: 노드마다 여기를 고친다 -------------------------------------------
NODE = "student1"        # fixedcam / tracker / student1~4 (common/message.py 의 VALID_NODES)
CAMERA = {"index": 0, "width": 640, "height": 480, "fps": 15}
SEATS = [1]              # 이 노드가 맡은 좌석 (1~4, 5 = 매트)
LOOP_FPS = 15            # 메인 루프 목표 속도
QUIET = True             # True: 처음 연결될 때만 출력 / False: 접속·끊김 로그 전부
FAKE_EVENTS = False      # True: 5초마다 가짜 손들기 start/end 전송 (ack 확인용)
# ---------------------------------------------------------------------------


def on_message(msg: dict):
    """hub -> 이 노드. (수신 스레드에서 불리므로 오래 걸리는 일은 하지 않는다)"""
    if msg["type"] == "ack" and QUIET:
        return                     # 조용한 모드에서는 ack 는 출력하지 않음
    print(f"  <- hub: {msg['type']} {msg['value']}")


def detect(t: float) -> dict:
    """TODO: 카메라 프레임을 보고 현재 상태를 돌려준다.
    지금은 가짜 값: FAKE_EVENTS 가 켜져 있으면 5초마다 손을 들었다 내렸다 한다."""
    return {"hand": FAKE_EVENTS and int(t) // 5 % 2 == 1}


def main():
    ap = argparse.ArgumentParser(description=f"{NODE} 노드")
    ap.add_argument("--server", default=None, help="hub IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(hub_host=args.server, hub_port=args.port)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    if QUIET:                      # 접속·재접속·끊김 로그 숨김 (오류만 표시)
        logging.getLogger("client").setLevel(logging.ERROR)

    client = NodeClient(NODE, cfg["hub_host"], int(cfg["hub_port"]), on_message=on_message)
    client.set_info({"camera": CAMERA,
                     "seats": [{"seat": s, "label": f"{s}번"} for s in SEATS]})
    client.start()                 # 접속·재접속·하트비트는 백그라운드에서 알아서 한다
    print(f"{NODE}: hub {cfg['hub_host']}:{cfg['hub_port']} 에 접속 중... (Ctrl+C 로 끝)")

    announced = False              # "연결됨" 을 한 번 출력했는지
    prev = None
    frames, t0 = 0, time.time()
    try:
        while True:
            start = time.time()
            if not announced and client.connected:
                print(f"{NODE}: hub 에 연결됨. 대기 중... (Ctrl+C 로 끝)")
                announced = True

            # TODO: frame = cam.read() 같은 카메라 읽기
            state = detect(start)

            # 사건은 "바뀐 순간"에만 보낸다 (매 프레임 보내지 않는다)
            if prev is not None and state["hand"] != prev["hand"]:
                value = {"state": "start" if state["hand"] else "end"}
                sent = client.send("hand_raise", seat=SEATS[0], value=value, conf=0.9)
                if not QUIET:
                    print(f"-> hand_raise {value['state']}" + ("" if sent else " (끊김: 재접속 후 전송)"))
            prev = state

            # fps 를 재서 하트비트에 싣는다
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
