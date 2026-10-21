"""
student_client_N 실행 파일 (학생 PC). VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.
(control 서버는 먼저 service/jarvis_control_service/run_hub.py 로 켜 둔다)

기본: 웹캠 판정 + control 전송 (monitor/seat_link_main.py 를 그대로 실행)
  - 졸음·하품·전방 주시 미흡·앞으로 숙임을 판정해 condition_summary {"focus_low", "posture_bad"} 를 10초마다 보낸다
  - fall_alert 가 오면 화면에 띄우고, f 또는 Enter 로 확인(fall_confirm)한다
  - 좌석 저장값이 없으면 시작할 때 바른 자세 10초 등록 → 방향 측정 (README 참고)
--no-camera: 카메라 없이 연결·알림 흐름만 시험 (터미널에서 Enter 로 확인, 고정값 전송)

터미널에서는 (어느 폴더에서 실행해도 된다):
  python3 run_client.py --seat 1                          # control IP·포트는 config/common.yaml
  python3 run_client.py --seat 1 --server 127.0.0.1       # 같은 PC에서 시험
  python3 run_client.py --seat 1 --rule --interval 5      # seat_link_main / monitor_main 옵션도 그대로 쓸 수 있다
  python3 run_client.py --seat 1 --no-camera [--focus-low] [--posture-bad]
같은 좌석으로 두 개를 동시에 띄우면 control 이 먼저 붙은 쪽을 끊으므로 좌석당 하나만 실행한다.
"""

import argparse
import logging
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))      # monitor/, seat_common/, seat_link/ (이 폴더)

from seat_link.control_link import VALID_SEATS, StudentLink   # noqa: E402

SEAT = 1                 # Run Code 로 실행할 때 좌석 번호 (옵션 --seat 가 있으면 그게 우선)
SUMMARY_INTERVAL_S = 10
STAGE_KO = {"neighbor": "옆자리", "all": "전체"}
THIRD_PARTY = {"cv2", "mediapipe", "torch", "torchvision", "numpy", "PIL"}   # 없으면 pip 설치 안내


# ---- 기본: 웹캠 판정 + 전송 ----------------------------------------------------
def run_camera(argv: list):
    if not any(a == "--seat" or a.startswith("--seat=") for a in argv):
        argv = ["--seat", str(SEAT)] + argv
    sys.argv = [sys.argv[0]] + argv              # seat_link_main 이 sys.argv 를 읽는다
    try:
        from monitor import seat_link_main
    except ImportError as e:
        top = (getattr(e, "name", None) or "").split(".")[0]
        hint = ("패키지 설치 (저장소 최상위에서): pip install -r requirements.txt -c constraints.txt"
                if top in THIRD_PARTY else
                "student_client 폴더의 파일이 빠졌는지 확인 (git pull / git status)")
        raise SystemExit(
            f"웹캠 판정 모듈을 불러오지 못했습니다: {e}\n"
            f"  {hint}\n"
            "  카메라 없이 연결만 시험하려면: python3 run_client.py --no-camera --seat N") from None
    seat_link_main.main()


# ---- --no-camera: 연결·알림 시험 ----------------------------------------------
def on_alert(seat, stage):
    if seat is None:
        print("<- 알림 해제")
    else:
        print(f"<- 쓰러짐 알림: {seat}번 좌석 ({STAGE_KO.get(stage, stage)}). Enter 로 확인")


def run_test_node(argv: list):
    ap = argparse.ArgumentParser(description="student_client_N 시험 노드 (카메라 없음)")
    ap.add_argument("--seat", type=int, choices=VALID_SEATS, default=SEAT)
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--focus-low", action="store_true", help="condition_summary 의 focus_low 를 true 로")
    ap.add_argument("--posture-bad", action="store_true", help="condition_summary 의 posture_bad 를 true 로")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)   # 재접속 로그는 숨김

    link = StudentLink(args.seat, args.server, args.port, on_alert=on_alert)
    link.start()
    print(f"{link.node} (카메라 없음): control {link.host}:{link.port} 에 접속 중... (Ctrl+C 로 끝)")

    stop = threading.Event()

    def summary_loop():
        while not stop.wait(SUMMARY_INTERVAL_S):
            link.send_summary(args.focus_low, args.posture_bad)

    threading.Thread(target=summary_loop, daemon=True).start()
    try:
        while True:
            input()
            link.confirm_fall()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        link.stop()


def main():
    argv = sys.argv[1:]
    if "--no-camera" in argv:
        run_test_node([a for a in argv if a != "--no-camera"])
    else:
        run_camera(argv)


if __name__ == "__main__":
    main()
