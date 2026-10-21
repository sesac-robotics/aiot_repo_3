"""강사 화면이 들고 있는 상태 (Control이 보낸 값의 사본)

원칙(Interface Spec 5.3): 화면은 event_update의 최신 값으로 덮어쓰기만 하고
상태를 직접 계산하지 않는다. 여기서 하는 건 '표시를 위한 정렬·필터'뿐이다.

Control(hub)이 Console 에 보내는 것 (common/protocol.py CONSOLE_TYPES):
  snapshot {events, queue, devices} · event_update · queue_state · device_update · class_condition · preset_saved
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSettings, pyqtSignal

from common import protocol as P

SEATS = P.VALID_SEATS
# 2×2 배치 (1 2 / 3 4), 옆자리 = 가로·세로만 (control.yaml 과 같게)
LAYOUT_NEIGHBORS = {1: (2, 3), 2: (1, 4), 3: (1, 4), 4: (2, 3)}


def settings() -> QSettings:
    """이 PC 에만 저장하는 화면 설정 (알림음 등). Control 에는 설정 저장 메시지가 없다."""
    return QSettings("jarvis", "instructor_console")


class Store(QObject):
    snapshot_done = pyqtSignal()
    seats_changed = pyqtSignal()
    event_changed = pyqtSignal(int, bool)        # event_id, 새 이벤트인지
    queue_changed = pyqtSignal()
    devices_changed = pyqtSignal()
    condition_changed = pyqtSignal()
    rest_suggested = pyqtSignal(float, float)    # ratio, since (hub 미지원 - 지금은 오지 않음)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seats: dict[int, dict] = {s: self._empty_seat(s) for s in SEATS}
        self.events: dict[int, dict] = {}        # event_update로 받은 것 (닫힌 것도 캐시 → 로그 검색에 씀)
        self.queue = {"active": None, "waiting": []}
        self.devices: dict[str, dict] = {}       # 노드 이름 → device_update
        self.condition: dict | None = None
        self.in_snapshot = False

    @staticmethod
    def _empty_seat(s):
        # 학생 이름(자리 등록)은 아직 Control 이 보내지 않는다 → 미등록으로 표시
        return {"seat": s, "neighbors": list(LAYOUT_NEIGHBORS[s]), "has_preset": False,
                "preset": None, "device": P.student_node(s), "student_name": ""}

    # ── 수신 처리 ───────────────────────────────────
    def apply(self, name: str, m: dict):
        if name == "snapshot":
            # 다시 접속하면 열린 이벤트·대기열·장비 상태를 통째로 새로 받는다 (닫힌 것은 로그용으로 유지)
            self.in_snapshot = True
            self.events = {k: v for k, v in self.events.items() if v["status"] == "closed"}
            for e in m.get("events", []):
                self.events[e["event_id"]] = e
            self.queue = m.get("queue") or {"active": None, "waiting": []}
            self.devices = {d["node"]: d for d in m.get("devices", [])}
            self.in_snapshot = False
            self.snapshot_done.emit()
            self.seats_changed.emit(); self.queue_changed.emit(); self.devices_changed.emit()
        elif name == "event_update":
            is_new = m["event_id"] not in self.events
            self.events[m["event_id"]] = m
            self.event_changed.emit(m["event_id"], is_new)
        elif name == "queue_state":
            self.queue = m
            self.queue_changed.emit()
        elif name == "device_update":
            self.devices[m["node"]] = m
            self.devices_changed.emit()
        elif name == "class_condition":
            self.condition = m
            self.condition_changed.emit()
        elif name == "preset_saved":             # 팬틸트 각도 등록 완료 {seat, pan, tilt}
            seat = self.seats.get(m.get("seat"))
            if seat:
                seat["has_preset"] = True
                seat["preset"] = (m.get("pan"), m.get("tilt"))
                self.seats_changed.emit()

    # ── 표시용 조회 ─────────────────────────────────
    def open_events(self, seat: int | None = None) -> list[dict]:
        evs = [e for e in self.events.values()
               if e["status"] != "closed" and (seat is None or e["seat"] == seat)]
        return sorted(evs, key=lambda e: (e["priority"], e["detected_at"]))

    def seat_top_event(self, seat: int) -> dict | None:
        evs = self.open_events(seat)
        return evs[0] if evs else None

    def unconfirmed(self) -> list[dict]:
        """확인 안 한 이벤트 (confirmed_at 없음 + 열려 있음), 최신순"""
        evs = [e for e in self.open_events() if not e["confirmed_at"]]
        return sorted(evs, key=lambda e: -e["detected_at"])

    def student_name(self, seat: int) -> str:
        s = self.seats.get(seat)
        return s["student_name"] if s else ""

    def student_device(self, seat: int) -> dict | None:
        return self.devices.get(P.student_node(seat))

    @staticmethod
    def alert_sound_on() -> bool:
        return settings().value("alert_sound", True, type=bool)
