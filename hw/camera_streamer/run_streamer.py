"""
camera_streamer 실행 파일. VS Code에서 이 파일을 열고 Run Code(▶)를 누르면 된다.

웹캠 영상을 JPEG 로 압축해 UDP 로 control(hub) 에 실시간으로 보낸다.
hub 는 저장하지 않고 instructor_console 로 그대로 넘긴다 (형식·포트는 common/protocol.py 의 VIDEO_*).
- 데이터그램 하나 = 헤더 16바이트(cam_id, frame_id, captured_at) + JPEG 한 장
- UDP 한 개(약 64KB)를 넘으면 품질을 낮춰 다시 압축하고, 그래도 크면 그 프레임은 건너뛴다.
- 카메라를 못 읽으면 2초마다 다시 연다. TCP 노드가 아니다.

터미널에서는:  python3 run_streamer.py [--cam 1] [--device 0] [--target 192.168.0.23]
  --target 을 안 주면 config/common.yaml 의 control_host 로 보낸다.
"""

import argparse
import socket
import sys
import time
from pathlib import Path

import cv2

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.config import load_config                                   # noqa: E402
from common.protocol import VIDEO_CAMS, VIDEO_HEADER, VIDEO_MAX_JPEG, VIDEO_PORTS   # noqa: E402

SEND_FPS = 15
WIDTH = 640                  # 보낼 영상 가로 크기 (세로는 비율대로)
JPEG_QUALITY = 70
FALLBACK_QUALITIES = (55, 40, 25)    # 64KB 를 넘을 때 차례로 낮춰 본다
REOPEN_INTERVAL_S = 2.0
READ_FAIL_LIMIT = 30         # 연속으로 이만큼 못 읽으면 카메라를 다시 연다
STATS_INTERVAL_S = 5.0


def open_camera(device: int):
    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)          # 쌓인 옛 프레임 대신 최신 프레임을 읽게
    return cap


def encode_jpeg(frame, width: int, quality: int):
    """프레임 → JPEG bytes (UDP 한 개에 들어가는 크기). 끝내 크면 None."""
    h, w = frame.shape[:2]
    if w > width:
        frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    for q in (quality, *(fq for fq in FALLBACK_QUALITIES if fq < quality)):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])
        if ok and len(buf) <= VIDEO_MAX_JPEG:
            return buf.tobytes()
    return None


def main():
    ap = argparse.ArgumentParser(description="camera_streamer (웹캠 -> UDP -> control)")
    ap.add_argument("--cam", type=int, choices=VIDEO_CAMS, default=1, help="카메라 번호 (1 고정, 2 팬틸트)")
    ap.add_argument("--device", type=int, default=0, help="카메라 장치 번호 (내장 웹캠은 보통 0)")
    ap.add_argument("--target", default=None, help="control IP (기본: config/common.yaml 의 control_host)")
    ap.add_argument("--fps", type=float, default=SEND_FPS)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--quality", type=int, default=JPEG_QUALITY, help="JPEG 품질 1~100")
    args = ap.parse_args()

    host = args.target or load_config()["control_host"]
    addr = (host, VIDEO_PORTS[args.cam])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    print(f"camera_streamer: cam{args.cam} (장치 {args.device}) -> udp {addr[0]}:{addr[1]} (Ctrl+C 로 끝)")

    cap = None
    frame_id = 0
    fails = 0
    sent = skipped = 0
    sent_bytes = 0
    t_stats = time.time()
    try:
        while True:
            start = time.time()
            if cap is None:
                cap = open_camera(args.device)
                if cap is None:
                    print(f"카메라 {args.device} 를 열 수 없음 - {REOPEN_INTERVAL_S:.0f}초 후 다시 시도")
                    time.sleep(REOPEN_INTERVAL_S)
                    continue
                print(f"카메라 {args.device} 열림")
                fails = 0

            ok, frame = cap.read()
            if not ok:
                fails += 1
                if fails >= READ_FAIL_LIMIT:
                    print("카메라에서 프레임을 읽지 못함 - 다시 연다")
                    cap.release()
                    cap = None
                time.sleep(0.05)                       # 실패 시 CPU 를 다 쓰지 않게
                continue
            fails = 0
            captured_at = time.time()

            jpeg = encode_jpeg(frame, args.width, args.quality)
            if jpeg is None:
                skipped += 1
            else:
                frame_id = (frame_id + 1) & 0xFFFFFFFF
                try:
                    sock.sendto(VIDEO_HEADER.pack(args.cam, frame_id, captured_at) + jpeg, addr)
                    sent += 1
                    sent_bytes += len(jpeg)
                except OSError as e:                   # 네트워크가 잠깐 끊겨도 계속 보낸다
                    print(f"전송 실패: {e}")

            now = time.time()
            if now - t_stats >= STATS_INTERVAL_S:
                span = now - t_stats
                avg_kb = sent_bytes / sent / 1024 if sent else 0
                print(f"cam{args.cam}: {sent / span:.1f}fps, 평균 {avg_kb:.0f}KB"
                      + (f", 너무 커서 건너뜀 {skipped}장" if skipped else ""))
                sent = skipped = sent_bytes = 0
                t_stats = now

            time.sleep(max(0.0, 1.0 / args.fps - (time.time() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        if cap is not None:
            cap.release()
        sock.close()


if __name__ == "__main__":
    main()
