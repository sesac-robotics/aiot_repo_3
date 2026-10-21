"""카메라 + 판정 스레드: monitor.monitor_main.Monitor를 그대로 돌리고, 화면에 줄 값만 시그널로 보낸다

- 판정 로직은 건드리지 않는다. Monitor.step(frame, now) 결과(state, 안내 줄)를 GUI로 넘길 뿐이다.
- Monitor와는 공개 함수만 쓴다 (monitor/calib_flow.py): register, next_zone, zone_step, labels,
  seat_calib.on_baseline_saved / on_zones_saved. Monitor 내부 이름에는 기대지 않는다.
- 영상은 화면에만 보여 주고 저장하지 않는다 (SR_006).
- 자리 등록: register() → Monitor.register() (바른 자세 10초). 영역 측정값이 없으면 Monitor가 이어서
  영역 측정을 시작하고, 학생은 [다음] 버튼(= Space 키)으로 영역마다 진행한다.
- 10초 요약: monitor/condition.py 의 ConditionLatch 로 모은다 (그동안 한 번이라도 확정되면 True, 개별 6개 + 묶음 2개).
"""
import queue
import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from monitor.condition import ConditionLatch, to_summary
from monitor.monitor_main import Monitor

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
        self._latch = ConditionLatch()

    # ---- GUI 쪽에서 부르는 함수 (어느 스레드에서 불러도 안전) ----------------
    def register(self):
        self.cmds.put("register")

    def next_zone(self):
        self.cmds.put("next")

    def stop(self):
        self._running = False
        self.wait(3000)

    def take_summary(self) -> dict:
        """10초마다 app이 가져감 → condition_summary 값. 가져가면 다시 False부터 모은다"""
        return to_summary(self._latch.snapshot())

    # ---- 판정 스레드 --------------------------------------------------------
    def _connect(self, monitor):
        """저장이 끝나면 GUI에 알리도록 Monitor의 알림 자리에 시그널을 건다"""
        monitor.seat_calib.on_baseline_saved = self.registered.emit
        monitor.seat_calib.on_zones_saved = self.zones_saved.emit
        self.labels_ready.emit(*monitor.labels())

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
        self._connect(monitor)
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
                self._publish(monitor.state, lines, frame, monitor.zone_step)
        finally:
            cap.release()
            monitor.close()

    def _do_commands(self, monitor, now):
        while not self.cmds.empty():
            cmd = self.cmds.get_nowait()
            if cmd == "register":
                monitor.register(now)
            elif cmd == "next":
                monitor.next_zone(now)

    def _publish(self, state, lines, frame, zone_step):
        self._latch.update(state)
        guide = [text for text, _ in lines] if state.get("calibrating") else []
        self.state_ready.emit({**state, "guide": guide, "zone_step": zone_step})
        frame = cv2.flip(frame, 1)                     # 거울처럼 보이게 (판정은 원본으로 이미 끝남)
        h, w = frame.shape[:2]
        img = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self.frame_ready.emit(img)