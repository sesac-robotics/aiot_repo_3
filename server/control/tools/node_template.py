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
    if (_p / "common" / "node_client.py").exists():
        sys.path.insert(0, str(_p))
        break

from common.config import load_config          # noqa: E402
from common.node_client import NodeClient      # noqa: E402

# ---- TODO: 노드마다 고친다 ----------------------------------------------------
NODE = "vision_service"  # 노드 이름 (common/protocol.py 의 VALID_NODES 중 하나)
SEAT = 1                 # 예시: 이 좌석의 손들기를 question 으로 보낸다 (1~4)
LOOP_FPS = 15            # 감지 루프 목표 속도 (초당 반복 횟수)
# ---------------------------------------------------------------------------


def on_message(msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림)."""
    if msg["type"] != "rx_ack":
        print(f"<- control: {msg['type']} {msg['value']}")


def detect() -> dict:
    """TODO: 카메라 프레임을 보고 현재 상태를 돌려준다."""
    return {"hand": False}


def main():
    # ---- 1. 명령줄 인자 / 설정 ------------------------------------------
    ap = argparse.ArgumentParser(description=f"{NODE} 노드")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    # ---- 2. control 접속 클라이언트 준비 --------------------------------
    client = NodeClient(NODE, cfg["control_host"], int(cfg["control_port"]), on_message=on_message)
    # vision_service 라면 카메라 상태를 하트비트에 싣는다: client.set_status(cam1_fps=.., cam2_fps=..)

    # ---- 3. 백그라운드 스레드 시작 -----------
    client.start()
    print(f"{NODE}: control {cfg['control_host']}:{cfg['control_port']} 에 접속 중...")

    # ---- 4. 감지 루프 ---------------------------------------------------
    connected_once = False
    prev = detect()                  # 직전 상태 (변화 감지용)
    frames, t0 = 0, time.time()      # fps 측정용
    try:
        while True:
            start = time.time()
            if not connected_once and client.connected:
                print(f"{NODE}: 연결됨. 대기 중... (Ctrl+C 로 끝)")
                connected_once = True

            # [이벤트 판정·전송] 판정이 확정되거나 풀린 순간(엣지)에만 한 번 보낸다.
            # 유지 시간(손 2초, 쓰러짐 3초 등)은 detect() 쪽에서 지킨다.
            # 끊겨 있으면 사건 메시지는 버퍼에 모았다가 재접속 후 다시 보낸다.
            state = detect()
            if state["hand"] != prev["hand"]:
                client.send("question", seat=SEAT,
                            value={"state": "start" if state["hand"] else "end"})
            prev = state

            # 1초마다 실제 루프 속도를 재서 NodeClient 에 알려 준다 -> 다음 heartbeat 에 실려 간다
            frames += 1
            if time.time() - t0 >= 1.0:
                client.set_fps(frames / (time.time() - t0))
                frames, t0 = 0, time.time()

            # LOOP_FPS 에 맞춰 남은 시간만큼 쉰다
            time.sleep(max(0.0, 1.0 / LOOP_FPS - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    main()
