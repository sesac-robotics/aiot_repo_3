"""
student_client_N 노드 실행 파일 (학생 화면). VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

- fall_alert 를 받으면 쓰러짐 알림을 띄운다 (stage: neighbor 옆자리 / all 전체).
- 학생이 확인 버튼을 누르면 fall_confirm 을 보낸다. fall_alert_cancel 을 받으면 알림을 내린다.
- condition_summary(집중 저하·자세 나쁨)를 10초마다 보낸다.

지금은 GUI 대신 터미널에 출력하고, Enter 를 누르면 마지막 알림을 확인한다.
터미널에서는:  python3 run_client.py --seat 1 [--server 127.0.0.1]
"""

import argparse
import logging
import sys
import threading
from pathlib import Path

# common/ 은 service/jarvis_control_service/ 안에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "service" / "jarvis_control_service"))

from common.config import load_config                    # noqa: E402
from common.node_client import NodeClient                # noqa: E402
from common.protocol import VALID_SEATS, student_node    # noqa: E402

SEAT = 1                 # Run Code 로 실행할 때 좌석 번호 (옵션 --seat 가 있으면 그게 우선)
SUMMARY_INTERVAL_S = 10

alert_seat = None        # 지금 떠 있는 쓰러짐 알림의 좌석


def on_message(msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림). TODO: GUI 갱신."""
    global alert_seat
    t, v = msg["type"], msg["value"]
    if t == "fall_alert":
        alert_seat = v["target_seat"]
        print(f"<- 쓰러짐 알림: {alert_seat}번 좌석 ({v['stage']}). Enter 로 확인")
    elif t == "fall_alert_cancel" and v["target_seat"] == alert_seat:
        print(f"<- 알림 해제: {alert_seat}번 좌석")
        alert_seat = None


def condition() -> dict:
    """TODO: 학생 노트북 카메라로 집중 저하·자세 나쁨을 판정한다."""
    return {"focus_low": False, "posture_bad": False}


def main():
    ap = argparse.ArgumentParser(description="student_client_N 노드")
    ap.add_argument("--seat", type=int, choices=VALID_SEATS, default=SEAT)
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    node = student_node(args.seat)
    client = NodeClient(node, cfg["control_host"], int(cfg["control_port"]), on_message=on_message)
    client.start()
    print(f"{node}: control {cfg['control_host']}:{cfg['control_port']} 에 접속 중... (Ctrl+C 로 끝)")

    stop = threading.Event()

    def summary_loop():
        while not stop.wait(SUMMARY_INTERVAL_S):
            client.send("condition_summary", seat=args.seat, value=condition())

    threading.Thread(target=summary_loop, daemon=True).start()
    try:
        while True:
            input()
            if alert_seat is not None:
                client.send("fall_confirm", seat=args.seat, value={"target_seat": alert_seat})
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        client.stop()


if __name__ == "__main__":
    main()
