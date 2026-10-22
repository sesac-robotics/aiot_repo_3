"""Control 연결: 팀 공용 NodeClient를 Qt 시그널로 감싼다 (통신 형식은 common/에만 있음)

- 받음: fall_alert {target_seat, stage}, fall_alert_cancel {target_seat}
- 보냄: fall_confirm {target_seat}, condition_summary (10초마다): 묶음 focus_low·posture_bad +
  개별 drowsy·yawn·not_forward·forward_lean·head_tilt·shoulder_tilt (값은 monitor/condition.py to_summary)
- event_id: Control이 fall_alert에 event_id를 실어 보내면 fall_confirm에 그대로 돌려준다 (지금 hub는 안 보냄).
  끊긴 동안 쌓였다 늦게 간 확인이 같은 자리의 '새' 쓰러짐을 확인하지 않게 Control이 가려낼 수 있도록.
- 보냄: seat_register {student_name, baseline, zones} — [자리 등록]. Control이 DB에 저장하고 강사 화면에 이름을 띄운다.
  끊긴 동안 눌렀으면 NodeClient가 모아 두었다가 다시 접속하면 보낸다. 학생 노트북에도 같이 저장한다.
- 하트비트에 노트북 카메라 상태(cam_ok)를 같이 싣는다 (강사 화면 학생 정보 창).
"""
import logging

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from common.node_client import NodeClient
from common.protocol import VALID_TYPES, student_node

log = logging.getLogger("student_gui")
CAN_REGISTER = "seat_register" in VALID_TYPES


class ControlLink(QObject):
    fall_alert = pyqtSignal(int, str, int)      # target_seat, stage ("neighbor" / "all"), event_id (없으면 0)
    fall_cancel = pyqtSignal(int, int)          # target_seat, event_id (없으면 0)
    connection_changed = pyqtSignal(bool)

    def __init__(self, seat: int, host: str, port: int):
        super().__init__()
        self.seat = seat
        self.client = NodeClient(student_node(seat), host, port, on_message=self._on_message)
        self.client.set_info({"seat": seat, "role": "student_gui"})
        self._connected = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(1000)

    def start(self):
        self.client.start()

    def stop(self):
        self.client.stop()

    def _check(self):
        now = self.client.connected
        if now != self._connected:
            self._connected = now
            self.connection_changed.emit(now)

    def _on_message(self, msg: dict):
        """NodeClient 수신 스레드에서 불림 → 시그널로 GUI 스레드에 넘김 (Qt가 알아서 큐에 넣음)"""
        t, v = msg["type"], msg["value"]
        if t == "fall_alert":
            self.fall_alert.emit(int(v["target_seat"]), str(v.get("stage", "neighbor")),
                                 int(v.get("event_id") or 0))
        elif t == "fall_alert_cancel":
            self.fall_cancel.emit(int(v["target_seat"]), int(v.get("event_id") or 0))

    # ---- 보내기 ---------------------------------------------------------
    def set_camera(self, cam_ok: bool, fps: float):
        self.client.set_status(cam_ok=int(cam_ok))
        self.client.set_fps(fps)

    def send_fall_confirm(self, target_seat: int, event_id: int = 0):
        value = {"target_seat": target_seat}
        if event_id:                            # Control이 준 경우에만 (지금 hub는 없는 키를 무시함)
            value["event_id"] = event_id
        self.client.send("fall_confirm", seat=self.seat, value=value)

    def send_summary(self, summary: dict):
        self.client.send("condition_summary", seat=self.seat, value=summary)

    def send_register(self, name: str, baseline: dict, zones: dict | None) -> bool:
        """자리 등록을 Control(중앙 DB)에 보낸다. 아직 protocol.py에 없으면 False"""
        if not CAN_REGISTER:
            log.info("seat_register 미지원 (protocol.py) → 학생 노트북에만 저장")
            return False
        return self.client.send("seat_register", seat=self.seat,
                                value={"student_name": name, "baseline": baseline, "zones": zones or {}})