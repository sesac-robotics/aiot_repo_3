"""
카메라 한 대 → UDP 송출. 실행 파일 두 개가 이 코드를 같이 쓴다:
  run_fixed_camera.py   고정 카메라(내장 웹캠, cam1)   -> 강사 화면 [전체 화면]
  run_pantilt_camera.py 팬틸트 카메라(USB 포트, cam2) -> 강사 화면 [시설 관리] 각도 등록 · 이벤트 창

- 프레임을 JPEG 로 압축해 control(hub) 의 카메라별 포트로 보낸다 (common/protocol.py 의 VIDEO_*).
- 데이터그램 하나 = 헤더 16바이트(cam_id, frame_id, captured_at) + JPEG 한 장
- UDP 한 개(약 64KB)를 넘으면 품질을 낮춰 다시 압축하고, 그래도 크면 그 프레임은 건너뛴다.
- 카메라를 못 열거나 계속 못 읽으면 2초마다 다시 연다 (USB 를 뽑았다 꽂아도 이어서 보냄).
"""

import argparse
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import cv2

from common.config import load_config
from common.protocol import VIDEO_HEADER, VIDEO_MAX_JPEG, VIDEO_PORTS

CAM_NAMES = {1: "고정 카메라(cam1)", 2: "팬틸트 카메라(cam2)"}

# 리눅스는 V4L2 로 바로 연다 (기본 백엔드는 없는 장치를 열 때 ffmpeg 등을 차례로 시도하며 긴 에러를 찍는다)
BACKEND = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
pass   # 열기 실패 경고는 아래에서 한 번만 안내

SEND_FPS = 15
WIDTH = 640                  # 보낼 영상 가로 크기 (세로는 비율대로)
JPEG_QUALITY = 70
FALLBACK_QUALITIES = (55, 40, 25)    # 64KB 를 넘을 때 차례로 낮춰 본다
REOPEN_INTERVAL_S = 2.0
READ_FAIL_LIMIT = 30         # 연속으로 이만큼 못 읽으면 카메라를 다시 연다
STATS_INTERVAL_S = 5.0


def parse_device(text: str):
    """'2' -> 2 (장치 번호), '/dev/video2' 같은 경로는 그대로."""
    return int(text) if text.isdigit() else text


def list_devices() -> str:
    """리눅스에서 연결된 카메라 이름 (장치 번호를 못 찾을 때 안내용)."""
    rows = []
    for d in sorted(Path("/sys/class/video4linux").glob("video*")):
        try:
            if (d / "index").read_text().strip() != "0":      # 영상이 아닌 메타데이터 장치는 건너뜀
                continue
            rows.append(f"  /dev/{d.name}: {(d / 'name').read_text().strip()}")
        except OSError:
            continue
    return "\n".join(rows) or "  (찾은 카메라 없음)"


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


class CameraStreamer:
    def __init__(self, cam: int, device, host: str, fps: float = SEND_FPS, width: int = WIDTH,
                 quality: int = JPEG_QUALITY):
        self.cam, self.device = cam, device
        self.addr = (host, VIDEO_PORTS[cam])
        self.fps, self.width, self.quality = fps, width, quality
        self.name = CAM_NAMES.get(cam, f"cam{cam}")

    def log(self, text: str):
        print(f"[{self.name}] {text}", flush=True)

    def _open(self):
        cap = cv2.VideoCapture(self.device, BACKEND)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)          # 쌓인 옛 프레임 대신 최신 프레임을 읽게
        return cap

    def run(self, stop: threading.Event):
        """stop 이 설정될 때까지 보낸다."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.log(f"장치 {self.device} -> udp {self.addr[0]}:{self.addr[1]}")
        cap = None
        frame_id = fails = 0
        sent = skipped = sent_bytes = 0
        t_stats = time.time()
        open_warned = False
        try:
            while not stop.is_set():
                start = time.time()
                if cap is None:
                    cap = self._open()
                    if cap is None:
                        if not open_warned:              # 같은 안내를 2초마다 반복하지 않게 한 번만
                            open_warned = True
                            self.log(f"장치 {self.device} 를 열 수 없음 - {REOPEN_INTERVAL_S:.0f}초마다 다시 시도\n"
                                     f"연결된 카메라:\n{list_devices()}")
                        stop.wait(REOPEN_INTERVAL_S)
                        continue
                    self.log(f"장치 {self.device} 열림")
                    open_warned = False
                    fails = 0

                ok, frame = cap.read()
                if not ok:
                    fails += 1
                    if fails >= READ_FAIL_LIMIT:
                        self.log("프레임을 읽지 못함 - 다시 연다")
                        cap.release()
                        cap = None
                    stop.wait(0.05)                      # 실패 시 CPU 를 다 쓰지 않게
                    continue
                fails = 0
                captured_at = time.time()

                jpeg = encode_jpeg(frame, self.width, self.quality)
                if jpeg is None:
                    skipped += 1
                else:
                    frame_id = (frame_id + 1) & 0xFFFFFFFF
                    try:
                        sock.sendto(VIDEO_HEADER.pack(self.cam, frame_id, captured_at) + jpeg, self.addr)
                        sent += 1
                        sent_bytes += len(jpeg)
                    except OSError as e:                 # 네트워크가 잠깐 끊겨도 계속 보낸다
                        self.log(f"전송 실패: {e}")

                now = time.time()
                if now - t_stats >= STATS_INTERVAL_S:
                    span = now - t_stats
                    avg_kb = sent_bytes / sent / 1024 if sent else 0
                    self.log(f"{sent / span:.1f}fps, 평균 {avg_kb:.0f}KB"
                             + (f", 너무 커서 건너뜀 {skipped}장" if skipped else ""))
                    sent = skipped = sent_bytes = 0
                    t_stats = now

                stop.wait(max(0.0, 1.0 / self.fps - (time.time() - start)))
        finally:
            if cap is not None:
                cap.release()
            sock.close()
            self.log("종료")


def run_camera(cam: int, default_device: str):
    """실행 파일(run_fixed_camera.py / run_pantilt_camera.py)의 main. 명령줄 옵션을 읽고 Ctrl+C 까지 보낸다."""
    name = CAM_NAMES.get(cam, f"cam{cam}")
    ap = argparse.ArgumentParser(description=f"{name} -> UDP -> control")
    ap.add_argument("--device", default=default_device,
                    help=f"카메라 장치 번호 또는 경로 (기본: {default_device})")
    ap.add_argument("--target", default=None, help="control IP (기본: config/common.yaml 의 control_host)")
    ap.add_argument("--fps", type=float, default=SEND_FPS)
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--quality", type=int, default=JPEG_QUALITY, help="JPEG 품질 1~100")
    args = ap.parse_args()

    host = args.target or load_config()["control_host"]
    stop = threading.Event()
    # Ctrl+C 를 예외 대신 stop 신호로 받는다 (카메라를 놓고(release) 끝나게)
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(f"{name}: Ctrl+C 로 끝", flush=True)
    CameraStreamer(cam, parse_device(args.device), host, args.fps, args.width, args.quality).run(stop)
