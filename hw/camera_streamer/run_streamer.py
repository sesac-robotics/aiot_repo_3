"""
camera_streamer 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.

카메라 영상을 UDP 로 vision_service 에 바로 보낸다 (control 을 거치지 않음, DEV-02).
TCP 노드가 아니다. 카메라 상태는 vision_service 하트비트의 cam1_fps, cam2_fps 로 실린다.

터미널에서는:  python3 run_streamer.py --cam 1 --target 192.168.0.23
"""

import argparse
import socket
import time

TARGET_PORT = {1: 6001, 2: 6002}     # TODO: vision_service 와 맞출 카메라별 UDP 포트
SEND_FPS = 15


def grab_jpeg(device: int):
    """TODO: 카메라에서 프레임 한 장을 읽어 JPEG bytes 로 돌려준다. 못 읽으면 None.
    UDP 패킷 하나(약 64KB)에 들어가게 크기·품질을 맞추거나 나눠 보낸다."""
    return None


def main():
    ap = argparse.ArgumentParser(description="camera_streamer")
    ap.add_argument("--cam", type=int, choices=(1, 2), default=1, help="카메라 번호 (1 고정, 2 추적)")
    ap.add_argument("--device", type=int, default=0, help="카메라 장치 번호")
    ap.add_argument("--target", default="127.0.0.1", help="vision_service IP")
    args = ap.parse_args()

    addr = (args.target, TARGET_PORT[args.cam])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"camera_streamer: cam{args.cam} -> udp {addr[0]}:{addr[1]} (Ctrl+C 로 끝)")
    try:
        while True:
            start = time.time()
            jpeg = grab_jpeg(args.device)
            if jpeg:
                sock.sendto(jpeg, addr)
            time.sleep(max(0.0, 1.0 / SEND_FPS - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
