"""카메라 + 판정 스레드: monitor.monitor_main.Monitor를 그대로 돌리고, 화면에 줄 값만 시그널로 보낸다

- 판정 로직은 건드리지 않는다. Monitor.step(frame, now) 결과(state, 안내 줄)를 GUI로 넘길 뿐이다.
- 영상은 화면에만 보여 주고 저장하지 않는다 (SR_006).
- 자리 등록: register() → Monitor.recalibrate() (바른 자세 10초). 영역 측정값이 없으면 Monitor가 이어서
  영역 측정을 시작하고, 학생은 [다음] 버튼(= Space 키)으로 영역마다 진행한다.
- 10초 요약: 그동안 한 번이라도 확정된 문제가 있으면 True (monitor/condition.py와 같은 방식).
"""
import queue
import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from monitor.monitor_main import Monitor

FOCUS_KEYS = {"drowsy", "yawn", "not_forward"}        # 집중 저하로 세는 얼굴 문제
REOPEN_AFTER = 25                                     # 연속 25번(약 5초) 못 읽으면 카메라를 다시 연다


class Engine(QThread):
    frame_ready = pyqtSignal(QImage)                  # 미리보기 (점만 그린 프레임)
    state_ready = pyqtSignal(dict)                    # {"present", "calibrating", "face", "posture", "guide", "zone_step"}
    registered = pyqtSignal(dict)                     # 바른 자세 등록 끝: 기준값 7개
    zones_saved = pyqtSignal(dict)                    # 영역 측정 끝: 영역값
    camera_failed = pyqtSignal(str)
    labels_ready = pyqtSignal(dict, dict)             # 얼굴·자세 판정 이름 → 한글

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.cmds = queue.Queue()                     # GUI → 판정 스레드 명령 ("register" / "next")
        self.cam_ok = False
        self.fps = 0.0
        self._running = True
        self._latch = {"focus_low": False, "posture_bad": False}

    # ---- GUI 쪽에서 부르는 함수 (어느 스레드에서 불러도 안전) ----------------
    def register(self):
        self.cmds.put("register")

    def next_zone(self):
        self.cmds.put("next")

    def stop(self):
        self._running = False
        self.wait(3000)

    def take_summary(self) -> dict:
        """10초마다 app이 가져감. 가져가면 다시 False부터 모은다"""
        out, self._latch = self._latch, {"focus_low": False, "posture_bad": False}
        return out

    # ---- 판정 스레드 --------------------------------------------------------
    def _hook(self, monitor):
        """Monitor의 저장 순간을 가로채 GUI에 알린다 (Monitor 코드는 수정하지 않음)"""
        on_baseline = monitor.on_baseline
        save_zones = monitor.seat_calib.save_zones

        def baseline_hook(b):
            on_baseline(b)
            self.registered.emit(dict(b))

        def zones_hook(zones, warnings=()):
            save_zones(zones, warnings)
            self.zones_saved.emit(dict(zones))

        monitor.on_baseline = baseline_hook
        monitor.seat_calib.save_zones = zones_hook

    def _labels(self, monitor):
        from seat_common.face_judge import FACE_LABELS
        self.labels_ready.emit(dict(FACE_LABELS), dict(monitor.plabels))

    def run(self):
        src = int(self.args.source) if str(self.args.source).isdigit() else self.args.source
        cap = cv2.VideoCapture(src)
        if not cap.isOpened():
            self.camera_failed.emit(f"카메라를 열 수 없습니다: {self.args.source}")
            return
        try:
            monitor = Monitor(self.args)
        except Exception as e:                        # 모델 파일이 없을 때 등
            cap.release()
            self.camera_failed.emit(f"판정 모델을 불러오지 못했습니다: {e}\n--rule --face-rule 로 실행해 보세요")
            return
        self._hook(monitor)
        self._labels(monitor)
        monitor.start(time.monotonic())
        t_prev = time.monotonic()
        fails = 0
        try:
            while self._running:
                ok, frame = cap.read()
                self.cam_ok = ok
                if not ok:
                    fails += 1
                    if fails % REOPEN_AFTER == 0:      # USB 카메라를 뺐다 꽂으면 다시 열어야 읽힌다
                        cap.release()
                        cap = cv2.VideoCapture(src)
                    time.sleep(0.2)
                    continue
                fails = 0
                now = time.monotonic()
                self.fps = 0.9 * self.fps + 0.1 / max(now - t_prev, 1e-3)
                t_prev = now
                self._do_commands(monitor, now)
                lines = monitor.step(frame, now)
                zone_step = monitor.face_stage.zone_calib.active
                self._publish(monitor.state, lines, frame, zone_step)
        finally:
            cap.release()
            monitor.close()

    def _do_commands(self, monitor, now):
        while not self.cmds.empty():
            cmd = self.cmds.get_nowait()
            if cmd == "register":
                monitor.recalibrate(now)
            elif cmd == "next":
                monitor.face_stage.go(now)

    def _publish(self, state, lines, frame, zone_step):
        face, posture = set(state.get("face", ())), set(state.get("posture", ()))
        if state.get("present") and not state.get("calibrating"):
            self._latch["focus_low"] |= bool(FOCUS_KEYS & face)
            self._latch["posture_bad"] |= bool(posture)
        guide = [text for text, _ in lines] if state.get("calibrating") else []
        self.state_ready.emit({**state, "guide": guide, "zone_step": zone_step})
        frame = cv2.flip(frame, 1)                     # 거울처럼 보이게 (판정은 원본으로 이미 끝남)
        h, w = frame.shape[:2]
        img = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self.frame_ready.emit(img)
