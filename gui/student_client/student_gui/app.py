"""학생 화면 실행 (student_client_N 노드 + 웹캠 판정 + GUI)

gui/student_client 폴더에서 실행 (control 서버는 먼저 켜 둔다):
  python -m student_gui.app --seat 1                      # 서버 주소는 config/common.yaml
  python -m student_gui.app --seat 1 --server 127.0.0.1
  python -m student_gui.app --seat 1 --rule --face-rule   # 학습 모델 없이 규칙만 (모델 파일이 없을 때)
monitor.monitor_main 옵션(--source, --posture-hold, --img-every ...)도 그대로 쓸 수 있다.
판정 구성은 monitor와 같다 (얼굴 v2 + 근거 보정, 자세 v6). 단 자세 확정 시간만 실사용 값 30초가 기본이다.

흐름: 시작 → 이 좌석 등록값이 없으면 [자리 등록] 창부터 → 평소 화면. 자리 등록은 student_gui/registration.py.
등록값(이름·바른 자세·영역)은 학생 노트북 data/seats/에 저장하고, seat_register로 Control(중앙 DB)에도 보낸다
(강사 화면에 학생 이름이 뜬다).
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))      # 저장소 최상위 (common/)

from PyQt6.QtCore import QTimer                                    # noqa: E402
from PyQt6.QtWidgets import QApplication                           # noqa: E402

from common.config import load_config                              # noqa: E402
from common.protocol import VALID_SEATS                            # noqa: E402
from gui.alert_sound import AlertSound                             # noqa: E402  (강사 화면과 같은 알림음)
from monitor.monitor_args import build_parser                      # noqa: E402
from student_gui.dialogs import FallAlertDialog, SeatMapDialog     # noqa: E402
from student_gui.engine import Engine                              # noqa: E402
from student_gui.link import ControlLink                           # noqa: E402
from student_gui.main_window import StudentWindow                  # noqa: E402
from student_gui.registration import RegisterFlow                  # noqa: E402

SUMMARY_INTERVAL_S = 10
POSTURE_HOLD_S = 30.0          # 자세 문제 확정 시간 (실사용 값). monitor_main(개발 확인용)은 3초


def parse_args():
    ap = build_parser()                                 # monitor 옵션 (--seat, --rule, --source ...)
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    ap.set_defaults(posture_hold=POSTURE_HOLD_S)
    args = ap.parse_args()
    if args.seat not in VALID_SEATS:
        ap.error(f"--seat 는 {VALID_SEATS} 중 하나")
    return args


class StudentApp:
    def __init__(self, args):
        cfg = load_config(control_host=args.server, control_port=args.port)
        self.seat = args.seat
        self.alerts: dict[int, FallAlertDialog] = {}

        self.win = StudentWindow(self.seat)
        self.sound = AlertSound(self.win)
        self.engine = Engine(args)
        self.link = ControlLink(self.seat, cfg["control_host"], int(cfg["control_port"]))
        # 학습 얼굴 모델이면 방향 측정값도 필요
        self.reg = RegisterFlow(self.seat, not args.face_rule, self.win, self.engine, self.link)

        self.engine.frame_ready.connect(self.win.show_frame)
        self.engine.state_ready.connect(self.on_state)
        self.engine.camera_failed.connect(self.on_camera_failed)
        self.engine.labels_ready.connect(self._set_labels)
        self.link.connection_changed.connect(self.win.set_connected)
        self.link.fall_alert.connect(self.on_fall_alert)
        self.link.fall_cancel.connect(self.on_fall_cancel)
        self.win.register_clicked.connect(self.reg.open)
        self.win.seat_clicked.connect(lambda: SeatMapDialog(self.seat, self.win).exec())

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(SUMMARY_INTERVAL_S * 1000)

    def start(self):
        self.win.show()
        self.link.start()
        self.engine.start()
        if self.reg.needed():
            QTimer.singleShot(300, self.reg.open)               # 처음 실행: 자리 등록부터

    def stop(self):
        self.timer.stop()
        for dlg in list(self.alerts.values()):           # 따로 뜬 창이라 직접 닫는다
            dlg.close()
        self.engine.stop()
        self.link.stop()

    def on_camera_failed(self, text: str):
        """판정 스레드가 끝났다 → 등록을 기다리게 두지 않고 바로 알린다"""
        self.win.show_camera_error(text)
        self.reg.fail(text)

    def _set_labels(self, face: dict, posture: dict):
        self.win.face_labels, self.win.posture_labels = face, posture

    def on_state(self, s: dict):
        self.win.show_state(s)
        self.reg.show_guide(s)

    # ---- 쓰러짐 확인 요청 ------------------------------------------------------
    def on_fall_alert(self, target_seat: int, stage: str, event_id: int):
        if target_seat == self.seat:
            return
        dlg = self.alerts.get(target_seat)
        if dlg is None:
            # 부모 없이 띄운다: 평소 화면을 최소화해 둬도 쓰러짐 창은 보이게
            dlg = FallAlertDialog(target_seat, stage, event_id)
            dlg.confirmed.connect(self.link.send_fall_confirm)
            dlg.finished.connect(lambda _, s=target_seat: self.alerts.pop(s, None))
            self.alerts[target_seat] = dlg
            dlg.show()
        else:
            dlg.set_stage(stage, event_id)           # 옆자리 → 반 전체로 단계만 올림
        self.sound.play()                            # 알림 끄기(내 상태 안내)와 상관없이 항상
        dlg.raise_()
        dlg.activateWindow()

    def on_fall_cancel(self, target_seat: int, event_id: int):
        dlg = self.alerts.get(target_seat)
        if dlg is None or (event_id and dlg.event_id and event_id != dlg.event_id):
            return                                   # 지난 쓰러짐의 해제는 지금 창을 닫지 않는다
        self.alerts.pop(target_seat, None)
        dlg.close()

    # ---- 10초마다 --------------------------------------------------------
    def tick(self):
        self.link.set_camera(self.engine.cam_ok, self.engine.fps)
        self.link.send_summary(self.engine.take_summary())


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("client").setLevel(logging.ERROR)
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)       # 쓰러짐 창이 떠 있어도 평소 화면을 닫으면 끝낸다
    app = StudentApp(args)
    app.win.closed.connect(qapp.quit)
    qapp.aboutToQuit.connect(app.stop)
    app.start()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()