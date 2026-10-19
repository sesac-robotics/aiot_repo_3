"""
instructor_console 노드 실행 파일 (강사 메인 대시보드). VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

화면은 상태를 계산하지 않는다. event_update 의 최신 값으로 덮어쓰기만 한다.
- confirm : 강사 "확인" -> 단계 알림 멈춤
- resolve : 강사 "완료" -> 이벤트 종료, 다음 대상

지금은 GUI 대신 터미널에 출력하고, 명령을 입력받는다.  예) c 17  (confirm),  r 17  (resolve)
터미널에서는:  python3 run_console.py [--server 127.0.0.1]
"""

import argparse
import logging
import sys
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import load_config          # noqa: E402
from common.node_client import NodeClient      # noqa: E402
from common.protocol import CONSOLE_NODE       # noqa: E402

events: dict[int, dict] = {}     # event_id -> help_event 한 행 (event_update 로 덮어씀)


def on_message(msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림). TODO: GUI 갱신."""
    t, v = msg["type"], msg["value"]
    if t == "event_update":
        events[v["event_id"]] = v
        print(f"<- event {v['event_id']}: seat {v['seat']} {v['kind']} {v['status']}")
    elif t == "snapshot":
        print(f"<- snapshot: {v}")
    elif t in ("queue_state", "device_update", "class_condition"):
        print(f"<- {t}: {v}")


def main():
    ap = argparse.ArgumentParser(description=f"{CONSOLE_NODE} 노드")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    client = NodeClient(CONSOLE_NODE, cfg["control_host"], int(cfg["control_port"]), on_message=on_message)
    client.start()
    print(f"{CONSOLE_NODE}: control {cfg['control_host']}:{cfg['control_port']} 에 접속 중...")
    print("명령: c <event_id> (확인), r <event_id> (완료), Ctrl+C 로 끝")
    try:
        while True:
            parts = input().split()
            if len(parts) == 2 and parts[0] in ("c", "r") and parts[1].isdigit():
                client.send("confirm" if parts[0] == "c" else "resolve",
                            value={"event_id": int(parts[1])})
            elif parts:
                print("명령: c <event_id> 또는 r <event_id>")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    main()
