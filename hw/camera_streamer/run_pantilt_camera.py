"""
팬틸트 카메라(USB 포트) 송출 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.

USB 포트에 연결한 팬틸트 카메라 영상을 UDP 로 control(hub) 에 실시간으로 보낸다 (cam2, udp 6002).
hub 는 저장하지 않고 instructor_console [시설 관리] 팬틸트 각도 등록 · 이벤트 창으로 넘긴다. TCP 노드가 아니다.
송출 코드는 streamer.py (고정 카메라 run_fixed_camera.py 와 같이 쓴다).

터미널에서는:  python3 run_pantilt_camera.py [--device 2] [--target 192.168.0.23]
  --target 을 안 주면 config/common.yaml 의 control_host 로 보낸다.
  USB 를 꽂는 순서에 따라 장치 번호가 바뀔 수 있다. 안 열리면 화면에 나오는 카메라 목록을 보고 --device 로 고친다.
"""

import sys
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from streamer import run_camera     # noqa: E402

CAM = 2
# 장치 번호 (리눅스: /dev/videoN 의 N). 이 PC: 2 = Generic HD camera(USB). 1·3 은 영상이 아닌 메타데이터 장치
DEVICE = "2"

if __name__ == "__main__":
    run_camera(CAM, DEVICE)
