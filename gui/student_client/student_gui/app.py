"""학생 화면 실행 (student_client_N 노드 + 웹캠 판정 + GUI)

gui/student_client 폴더에서 실행 (control 서버는 먼저 켜 둔다):
  python -m student_gui.app --seat 1                      # 서버 주소는 config/common.yaml
  python -m student_gui.app --seat 1 --server 127.0.0.1
  python -m student_gui.app --seat 1 --rule --face-rule   # 학습 모델 없이 규칙만 (모델 파일이 없을 때)
monitor.monitor_main 옵션(--source, --posture-hold, --img-every ...)도 그대로 쓸 수 있다.

흐름: 시작 → 이 좌석 등록값이 없으면 [자리 등록] 창부터 → 평소 화면.
등록값(이름·바른 자세·영역)은 지금은 학생 노트북 data/seats/에 저장한다. seat_register가 protocol.py에
추가되면 Control(중앙 DB)에도 보낸다.
"""
import json
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
from monitor.seat_store import DEFAULT_DIR, open_store             # noqa: E402
from student_gui.dialogs import FallAlertDialog, RegisterDialog, SeatMapDialog   # noqa: E402
from student_gui.engine import Engine                              # noqa: E402
from student_gui.link import ControlLink                           # noqa: E402
from student_gui.main_window import StudentWindow                  # noqa: E402

SUMMARY_INTERVAL_S = 10


def parse_args():
    ap = build_parser()                                 # monitor 옵션 (--seat, --rule, --source ...)
    ap.add_argument("--server", default=None, help="control IP (기본: config/common.yaml)")
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()
    if args.seat not in VALID_SEATS:
        ap.error(f"--seat 는 {VALID_SEATS} 중 하나")
    return args


class Profile:
    """좌석에 등록된 학생 이름 (data/seats/seat_N_student.json, git 제외 폴더)"""

    def __init__(self, seat):
        self.path = DEFAULT_DIR / f"seat_{seat}_student.json"

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("student_name")
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, name):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"student_name": name}, ensure_ascii=False), encoding="utf-8")


class StudentApp:
    def __init__(self, args):
        cfg = load_config(control_host=args.server, control_port=args.port)
        self.seat = args.seat
        self.need_zones = not args.face_rule                 # 학습 얼굴 모델이면 영역 측정값이 필요
        self.profile = Profile(self.seat)
        self.name = self.profile.load()
        self.pending_name = None
        self.reg_dialog = None
        self.camera_error = None                             # 카메라·모델을 못 열었으면 그 사유
        self.alerts: dict[int, FallAlertDialog] = {}

        self.win = StudentWindow(self.seat)
        self.win.set_name(self.name)
        self.sound = AlertSound(self.win)
        self.engine = Engine(args)
        self.link = ControlLink(self.seat, cfg["control_host"], int(cfg["control_port"]))

        self.engine.frame_ready.connect(self.win.show_frame)
        self.engine.state_ready.connect(self.on_state)
        self.engine.registered.connect(self.on_registered)
        self.engine.zones_saved.connect(self.on_zones)
        self.engine.camera_failed.connect(self.on_camera_failed)
        self.engine.labels_ready.connect(self._set_labels)
        self.link.connection_changed.connect(self.win.set_connected)
        self.link.fall_alert.connect(self.on_fall_alert)
        self.link.fall_cancel.connect(self.on_fall_cancel)
        self.win.register_clicked.connect(self.open_register)
        self.win.seat_clicked.connect(lambda: SeatMapDialog(self.seat, self.win).exec())

        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.timer.start(SUMMARY_INTERVAL_S * 1000)

    def start(self):
        self.win.show()
        self.link.start()
        self.engine.start()
        baseline, zones = open_store().load(self.seat)
        if not (self.name and baseline) or (self.need_zones and not zones):
            QTimer.singleShot(300, self.open_register)          # 처음 실행: 자리 등록부터

    def stop(self):
        self.timer.stop()
        for dlg in list(self.alerts.values()):           # 따로 뜬 창이라 직접 닫는다
            dlg.close()
        self.engine.stop()
        self.link.stop()

    def on_camera_failed(self, text: str):
        """판정 스레드가 끝났다 → 등록을 기다리게 두지 않고 바로 알린다"""
        self.camera_error = text
        self.win.show_camera_error(text)
        if self.reg_dialog is not None:
            self.reg_dialog.fail(text)

    def _set_labels(self, face: dict, posture: dict):
        self.win.face_labels, self.win.posture_labels = face, posture

    # ---- 자리 등록 ---------------------------------------------------------
    def open_register(self):
        if self.reg_dialog is not None:
            self.reg_dialog.raise_()
            return
        self.reg_dialog = RegisterDialog(self.seat, self.name or "", self.win)
        self.reg_dialog.start_requested.connect(self._start_register)
        self.reg_dialog.next_requested.connect(self.engine.next_zone)
        self.reg_dialog.finished.connect(self._register_closed)
        self.reg_dialog.show()
        if self.camera_error:
            self.reg_dialog.fail(self.camera_error)

    def _start_register(self, name):
        if self.camera_error:
            self.reg_dialog.fail(self.camera_error)
            return
        self.pending_name = name
        self.engine.register()

    def _register_closed(self, _):
        self.reg_dialog, self.pending_name = None, None

    def on_state(self, s: dict):
        self.win.show_state(s)
        if self.reg_dialog is not None and self.reg_dialog.measuring:
            self.reg_dialog.show_guide(s.get("guide", []), s.get("zone_step", False))

    def on_registered(self, baseline: dict):
        if not self.pending_name:                    # c 키 등 다른 경로로 다시 잰 경우: 이름은 그대로
            return
        self.name = self.pending_name
        self.profile.save(self.name)
        self.win.set_name(self.name)
        _, zones = open_store().load(self.seat)
        sent = self.link.send_register(self.name, baseline, zones)
        if self.reg_dialog is not None:
            where = "중앙 서버에 저장했습니다." if sent else "이 노트북에 저장했습니다."
            more_zones = self.need_zones and not zones
            more = "\n이어서 보는 방향을 잽니다. 안내에 따라 [다음]을 눌러 주세요." if more_zones else ""
            # 영역 측정이 이어지면 [닫기]는 숨기고 안내를 계속 받는다 (닫으면 측정은 계속되는데 안내만 사라짐)
            self.reg_dialog.finish(f"{self.name}님, 바른 자세를 등록했어요. {where}{more}", closable=not more_zones)

    def on_zones(self, zones: dict):
        baseline, _ = open_store().load(self.seat)
        if self.name and baseline:                   # 영역까지 끝났으면 영역값을 담아 다시 보냄
            self.link.send_register(self.name, baseline, zones)
        if self.reg_dialog is not None:
            self.reg_dialog.finish("등록이 모두 끝났어요. [닫기]를 눌러 주세요.")

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
