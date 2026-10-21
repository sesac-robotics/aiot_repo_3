"""
vision_service 노드 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

- 카메라 영상은 camera_streamer 가 UDP 로 바로 보낸다 (control 을 거치지 않음).
- 사건(fall/question/away)은 판정이 확정·해제되는 순간 한 번만 보낸다. 유지 시간은 여기서 지킨다.
- track_target 을 받으면 그 좌석을 추적하며 track_offset 을 10~15회/초 보낸다.
- explain_request 를 받으면 LLM(Gemini 기본, Ollama 백업) 설명을 explain_text 로 돌려준다.

터미널에서는:  python3 run_assistant.py [--server 127.0.0.1]
"""

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import load_config          # noqa: E402
from common.node_client import NodeClient      # noqa: E402
from common.protocol import VISION_NODE        # noqa: E402

LOOP_FPS = 15            # 감지 루프 목표 속도 (초당 반복 횟수)

track_seat = None        # 지금 추적할 좌석 (control 의 track_target 으로 바뀜)


def on_message(client: NodeClient, msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림)."""
    global track_seat
    t, v = msg["type"], msg["value"]
    if t == "track_target":
        track_seat = v.get("seat")
        print(f"<- track_target: {track_seat}")
    elif t == "explain_request":
        # LLM 호출은 느리므로 따로 돌린다 (수신 스레드를 막으면 track_target 이 늦게 온다)
        threading.Thread(target=explain, args=(client, v), daemon=True).start()


def explain(client: NodeClient, req: dict):
    """TODO: LLM 으로 상황 설명 만들기 (Gemini -> 실패하면 Ollama -> 실패하면 기본 문구).
    text 는 꼭 문자열로 보낸다 (control 이 문자열이 아니면 버림)."""
    client.send("explain_text", value={"event_id": req["event_id"],
                                       "text": f"{req['seat']}번 좌석 {req['kind']}", "mode": "default"})


def detect() -> dict:
    """TODO: 카메라 프레임을 보고 좌석별 상태를 돌려준다. 예: {(2, "question"): True}
    지금 켜진 것만 돌려줘도 된다 (빠진 키는 False 로 보고 end 를 보낸다)."""
    return {}


def find_offset(seat: int):
    """TODO: 추적 카메라 화면에서 대상의 중심 오차 (dx, dy) (-1~1). 못 찾으면 None."""
    return None


def main():
    ap = argparse.ArgumentParser(description=f"{VISION_NODE} 노드")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    client = NodeClient(VISION_NODE, cfg["control_host"], int(cfg["control_port"]),
                        on_message=lambda m: on_message(client, m))
    client.start()
    print(f"{VISION_NODE}: control {cfg['control_host']}:{cfg['control_port']} 에 접속 중... (Ctrl+C 로 끝)")

    prev: dict = {}                  # 직전 상태 (변화 감지용)
    frames, t0 = 0, time.time()      # fps 측정용
    try:
        while True:
            start = time.time()

            # [사건] 상태가 바뀐 순간(엣지)에만 start/end 를 보낸다
            state = detect()
            for seat, kind in prev.keys() | state.keys():      # 결과에서 빠진 것은 False (end)
                on = state.get((seat, kind), False)
                if on != prev.get((seat, kind), False):
                    client.send(kind, seat=seat, value={"state": "start" if on else "end"})
            prev = state

            # [추적] control 이 정한 좌석만 따라간다. seat 을 꼭 채운다.
            if track_seat is not None:
                off = find_offset(track_seat)
                value = {"found": False} if off is None else {"found": True, "dx": off[0], "dy": off[1]}
                client.send("track_offset", seat=track_seat, value=value)

            frames += 1
            if time.time() - t0 >= 1.0:
                client.set_fps(frames / (time.time() - t0))
                client.set_status(cam1_fps=0.0, cam2_fps=0.0)     # TODO: 실제 카메라 수신 fps
                frames, t0 = 0, time.time()

            time.sleep(max(0.0, 1.0 / LOOP_FPS - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    main()
