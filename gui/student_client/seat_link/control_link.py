"""학생 노드 ↔ control(hub) 연결: common/node_client.NodeClient 위의 얇은 껍데기 (바이너리 프레임 v3, 포트 5000)

seat_link_main(웹캠 판정)과 run_client(카메라 없는 시험 노드)가 같이 쓴다.
- 노드 이름: student_client_<좌석>, 좌석은 1~4 (common/protocol.py VALID_SEATS)
- control IP·포트: 저장소 최상위 config/common.yaml (명령줄 --server/--port 가 있으면 그게 우선)
- 보내는 것: condition_summary {"focus_low": bool, "posture_bad": bool}  (명세: jarvis_control_service/CLAUDE.md)
             fall_confirm {"target_seat": N}                            (학생이 쓰러짐 알림 확인)
- 받는 것  : fall_alert {"target_seat", "stage"}, fall_alert_cancel {"target_seat"}

같은 좌석 번호로 두 프로그램을 동시에 띄우면 control 이 먼저 붙은 연결을 끊는다 (좌석당 하나만 실행).
"""
import logging
import sys
import threading
from pathlib import Path

# common/ 은 저장소 최상위(aiot_repo_3/)에 있다
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from common.config import load_config                    # noqa: E402
from common.node_client import NodeClient                # noqa: E402
from common.protocol import VALID_SEATS, student_node    # noqa: E402

log = logging.getLogger("control_link")


class StudentLink:
    def __init__(self, seat: int, server=None, port=None, on_alert=None):
        """on_alert(alert_seat_or_None, stage_or_None): 쓰러짐 알림이 뜨거나 내려갈 때 (수신 스레드에서 불림)"""
        if seat not in VALID_SEATS:
            raise ValueError(f"좌석은 {VALID_SEATS} 중 하나여야 합니다: {seat}")
        cfg = load_config(control_host=server, control_port=port)
        self.seat = seat
        self.node = student_node(seat)
        self.host, self.port = cfg["control_host"], int(cfg["control_port"])
        self.on_alert = on_alert
        self._lock = threading.Lock()
        self.alert_seat = None          # 지금 떠 있는 쓰러짐 알림의 좌석
        self.alert_stage = None
        self.sent = 0                   # 보낸 condition_summary 수
        self.dropped = 0                # 끊겨서 못 보낸 수 (상태 요약이라 다시 보내지 않음)
        self.client = NodeClient(self.node, self.host, self.port, on_message=self._on_message)

    # ---- 수명 ---------------------------------------------------------
    def start(self):
        self.client.start()
        log.info("%s: control %s:%d 에 접속 중", self.node, self.host, self.port)

    def stop(self):
        self.client.stop()

    @property
    def connected(self) -> bool:
        return self.client.connected

    def set_fps(self, fps: float):
        self.client.set_fps(fps)

    # ---- 보내기 -------------------------------------------------------
    def send_summary(self, focus_low: bool, posture_bad: bool) -> bool:
        ok = self.client.send("condition_summary", seat=self.seat,
                              value={"focus_low": bool(focus_low), "posture_bad": bool(posture_bad)})
        if ok:
            self.sent += 1
        else:
            self.dropped += 1
        return ok

    def confirm_fall(self) -> bool:
        """떠 있는 쓰러짐 알림을 확인했다고 보낸다. 알림이 없으면 아무것도 안 함."""
        with self._lock:
            target = self.alert_seat
        if target is None:
            return False
        self.client.send("fall_confirm", seat=self.seat, value={"target_seat": target})
        log.info("쓰러짐 확인 보냄: %d번 좌석", target)
        return True

    # ---- 받기 ---------------------------------------------------------
    def _on_message(self, msg: dict):
        t, v = msg["type"], msg["value"]
        if t == "fall_alert":
            with self._lock:
                self.alert_seat, self.alert_stage = v.get("target_seat"), v.get("stage")
            log.info("쓰러짐 알림: %s번 좌석 (%s)", self.alert_seat, self.alert_stage)
        elif t == "fall_alert_cancel":
            with self._lock:
                if v.get("target_seat") != self.alert_seat:
                    return
                self.alert_seat = self.alert_stage = None
            log.info("쓰러짐 알림 해제: %s번 좌석", v.get("target_seat"))
        else:
            return
        if self.on_alert:
            self.on_alert(self.alert_seat, self.alert_stage)
