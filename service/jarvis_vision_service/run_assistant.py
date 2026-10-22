"""
vision_service 노드 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

- 카메라 영상은 camera_streamer 가 UDP 로 바로 보낸다 (control 을 거치지 않음).
- 사건(fall/question/away)은 판정이 확정·해제되는 순간 한 번만 보낸다. 유지 시간은 여기서 지킨다.
- track_target 을 받으면 그 좌석을 추적하며 track_offset 을 10~15회/초 보낸다.
- explain_request 를 받으면 LLM(Gemini 기본, Ollama 백업) 설명을 explain_text 로 돌려준다.
- [질의] ask_request 를 받으면 ask.py 가 LLM tool calling 으로 답을 만들어 ask_answer 로 돌려준다
  (도구는 tool_call 로 control 에 실행을 맡긴다).
  control 은 상황이 바뀔 때마다(새 사건, 쓰러짐 15/30초, 일어남, 손 내림, 자리비움 미복귀) 다시 요청한다.

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

import ask                                     # noqa: E402  [질의] 강사 자연어 질의 + tool calling
import llm                                     # noqa: E402  AI 조교 설명 (Gemini → Ollama → 기본 문구)
from assistant_config import load_assistant_config   # noqa: E402

LOOP_FPS = 15            # 감지 루프 목표 속도 (초당 반복 횟수)

track_seat = None        # 지금 추적할 좌석 (control 의 track_target 으로 바뀜)

LLM_CFG = load_assistant_config()                                     # config/assistant.yaml + .env
explain_slots = threading.Semaphore(int(LLM_CFG.get("max_concurrent", 2)))   # 동시 설명 요청 수 제한
asker: "ask.AskWorker | None" = None                                  # [질의] main() 에서 client 와 함께 만든다


def on_message(client: NodeClient, msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림)."""
    global track_seat
    t, v = msg["type"], msg["value"]
    if asker is not None and asker.on_message(msg):     # [질의] ask_request / tool_result
        return
    if t == "track_target":
        track_seat = v.get("seat")
        print(f"<- track_target: {track_seat}")
    elif t == "explain_request":
        # LLM 호출은 느리므로 따로 돌린다 (수신 스레드를 막으면 track_target 이 늦게 온다)
        threading.Thread(target=explain, args=(client, v), daemon=True).start()


def explain(client: NodeClient, req: dict):
    """LLM 으로 상황 설명을 만들어 explain_text 로 돌려준다 (Gemini -> Ollama -> 기본 문구).
    llm.explain_event 는 예외를 던지지 않고 항상 (문자열, "gemini"|"ollama"|"default") 를 준다.
    text 는 꼭 문자열로 보낸다 (control 이 문자열이 아니면 버림)."""
    with explain_slots:                     # 사건이 몰려도 LLM 을 동시에 너무 많이 부르지 않게
        try:
            text, mode = llm.explain_event(req, LLM_CFG)
        except Exception:                   # 혹시 모를 오류에도 기본 문구는 꼭 보낸다
            logging.getLogger("llm").exception("설명 실패 event=%s", req.get("event_id"))
            text, mode = llm.default_text(req), "default"
    value = {"event_id": req["event_id"], "text": str(text), "mode": mode}
    if "seq" in req:                        # control 이 늦게 온 옛 응답을 거를 수 있게 그대로 돌려준다
        value["seq"], value["situation"] = req["seq"], req.get("situation")
    client.send("explain_text", value=value)
    print(f"-> explain_text event={req['event_id']} {req.get('situation', 'detect')} [{mode}] {text}")


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
    global asker
    asker = ask.AskWorker(client, LLM_CFG, on_answer=lambda req, res: print(
        f"-> ask_answer #{req['ask_id']} [{res['mode']} {res['took_s']}s, 도구 {len(res['tools'])}번] {res['text'][:80]}"))
    client.start()

    # Ollama 모델을 미리 GPU 에 올려 둔다 (첫 설명이 느리지 않게). 감지 루프를 막지 않게 따로 돌린다.
    print(f"AI 조교: gemini={LLM_CFG['gemini_model'] if LLM_CFG.get('gemini_api_key') else '키 없음(건너뜀)'}"
          f", ollama={LLM_CFG['ollama_model']}")
    if LLM_CFG.get("ollama_warmup", True):
        threading.Thread(target=llm.warmup, args=(LLM_CFG,), daemon=True).start()
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
