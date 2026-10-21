"""
고정 카메라(내장 웹캠) 송출 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.

웹캠 영상을 UDP 로 control(hub) 에 실시간으로 보낸다 (cam1, udp 6001).
hub 는 저장하지 않고 instructor_console [전체 화면] 탭으로 넘긴다. TCP 노드가 아니다.
송출 코드는 streamer.py (팬틸트 카메라 run_pantilt_camera.py 와 같이 쓴다).

터미널에서는:  python3 run_fixed_camera.py [--device 0] [--target 192.168.0.23]
  --target 을 안 주면 config/common.yaml 의 control_host 로 보낸다.
"""

import sys
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from streamer import run_camera     # noqa: E402

CAM = 1
# 장치 번호 (리눅스: /dev/videoN 의 N). 이 PC: 0 = HP Wide Vision HD Camera(내장 웹캠)
DEVICE = "0"

if __name__ == "__main__":
    run_camera(CAM, DEVICE)
