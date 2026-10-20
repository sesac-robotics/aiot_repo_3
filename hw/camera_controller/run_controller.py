"""
camera_controller 노드 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

control 의 pan_tilt 명령을 Serial 로 Arduino 에 전달하고, 적용 결과를 pan_tilt_state 로 돌려준다.
시리얼 포트는 이 노드만 잡는다.
- {"mode":"preset","seat":2,"pan":80,"tilt":85} : 좌석 프리셋 (각도는 control 이 control.yaml 에서 읽어 실어 보냄)
- {"mode":"nudge","dpan":1.5,"dtilt":-0.8} : 지금 각도에서 조금 움직임
- {"mode":"home"}                       : 기본 위치

터미널에서는:  python3 run_controller.py [--server 127.0.0.1] [--serial /dev/ttyUSB0]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import load_config          # noqa: E402
from common.node_client import NodeClient      # noqa: E402
from common.protocol import CAMERA_NODE        # noqa: E402

SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 115200


def apply_pan_tilt(value: dict) -> dict:
    """TODO: pan_tilt 명령을 Serial 로 Arduino 에 보내고 OK 응답을 기다린다.
    돌려주는 값은 그대로 pan_tilt_state 로 간다: {"pan":95,"tilt":80,"ok":true}"""
    print(f"pan_tilt: {value}")
    return {"pan": None, "tilt": None, "ok": False}


def on_message(client: NodeClient, msg: dict):
    """control 이 보낸 메시지 (수신 스레드에서 불림)."""
    if msg["type"] == "pan_tilt":
        client.send("pan_tilt_state", value=apply_pan_tilt(msg["value"]))


def main():
    ap = argparse.ArgumentParser(description=f"{CAMERA_NODE} 노드")
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--serial", default=SERIAL_PORT, help=f"Arduino 시리얼 포트 (기본: {SERIAL_PORT})")
    args = ap.parse_args()
    cfg = load_config(control_host=args.server, control_port=args.port)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    # TODO: 시리얼 열기 (pyserial)  serial.Serial(args.serial, BAUD, timeout=1)

    client = NodeClient(CAMERA_NODE, cfg["control_host"], int(cfg["control_port"]),
                        on_message=lambda m: on_message(client, m))
    client.start()
    print(f"{CAMERA_NODE}: control {cfg['control_host']}:{cfg['control_port']} 에 접속 중... (Ctrl+C 로 끝)")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    main()
