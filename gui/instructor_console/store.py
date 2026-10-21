"""강사 화면이 들고 있는 상태 (Control이 보낸 값의 사본)

원칙(Interface Spec 5.3): 화면은 event_update의 최신 값으로 덮어쓰기만 하고
상태를 직접 계산하지 않는다. 여기서 하는 건 '표시를 위한 정렬·필터'뿐이다.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from common import protocol as P

SEATS = (1, 2, 3, 4)
# 2×2 배치 (1 2 / 3 4), 옆자리 = 가로·세로만 (control.yaml)
LAYOUT_NEIGHBORS = {1: (2, 3), 2: (1, 4), 3: (1, 4), 4: (2, 3)}


def neighbors_to_mask(seats) -> int:
    """옆자리 비트마스크: 좌석 n → bit (n-1)"""
    m = 0
    for s in seats:
        m |= 1 << (s - 1)
    return m


def mask_to_neighbors(mask: int) -> list[int]:
    return [s for s in SEATS if mask & (1 << (s - 1))]


class Store(QObject):
    snapshot_done = pyqtSignal()
    seats_changed = pyqtSignal()
    event_changed = pyqtSignal(int, bool)        # event_id, 새 이벤트인지
    queue_changed = pyqtSignal()
    devices_changed = pyqtSignal()
    condition_changed = pyqtSignal()
    rest_suggested = pyqtSignal(float, float)    # ratio, since
    settings_changed = pyqtSignal()
    log_result = pyqtSignal(dict)
    clip_info = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.seats: dict[int, dict] = {s: self._empty_seat(s) for s in SEATS}
        self.events: dict[int, dict] = {}        # event_update로 받은 것 (닫힌 것도 캐시)
        self.queue = {"active": 0, "waiting": []}
        self.devices: dict[int, dict] = {}
        self.condition: dict | None = None
        self.settings: dict[str, str] = {}
        self.in_snapshot = False

    @staticmethod
    def _empty_seat(s):
        return {"seat": s, "neighbors": neighbors_to_mask(LAYOUT_NEIGHBORS[s]), "has_preset": 0,
                "device": "", "reg_id": 0, "student_name": ""}

    # ── 수신 처리 ───────────────────────────────────
    def apply(self, name: str, m: dict):
        if name == "snapshot_begin":
            self.in_snapshot = True
            # 다시 접속하면 열린 이벤트·장비 상태는 스냅샷으로 새로 받는다
            self.events = {k: v for k, v in self.events.items() if v["status"] == P.ST_CLOSED}
            self.devices.clear()
        elif name == "snapshot_end":
            self.in_snapshot = False
            self.snapshot_done.emit()
            self.seats_changed.emit(); self.queue_changed.emit()
            self.devices_changed.emit(); self.settings_changed.emit()
        elif name == "seat_info":
            if m["seat"] in self.seats:
                self.seats[m["seat"]] = m
                if not self.in_snapshot:
                    self.seats_changed.emit()
        elif name == "event_update":
            is_new = m["event_id"] not in self.events and not self.in_snapshot
            self.events[m["event_id"]] = m
            self.event_changed.emit(m["event_id"], is_new)
        elif name == "queue_state":
            self.queue = m
            if not self.in_snapshot:
                self.queue_changed.emit()
        elif name == "device_update":
            self.devices[m["node"]] = m
            if not self.in_snapshot:
                self.devices_changed.emit()
        elif name == "class_condition":
            self.condition = m
            self.condition_changed.emit()
        elif name == "rest_suggest":
            self.rest_suggested.emit(m["ratio"], m["since"])
        elif name == "app_setting":
            self.settings[m["key"]] = m["value"]
            if not self.in_snapshot:
                self.settings_changed.emit()
        elif name == "log_result":
            self.log_result.emit(m)
        elif name == "clip_info":
            self.clip_info.emit(m)

    # ── 표시용 조회 ─────────────────────────────────
    def open_events(self, seat: int | None = None) -> list[dict]:
        evs = [e for e in self.events.values()
               if e["status"] != P.ST_CLOSED and (seat is None or e["seat"] == seat)]
        return sorted(evs, key=lambda e: (e["kind"], e["detected_at"]))   # priority = kind

    def seat_top_event(self, seat: int) -> dict | None:
        evs = self.open_events(seat)
        return evs[0] if evs else None

    def unconfirmed(self) -> list[dict]:
        """확인 안 한 이벤트 (confirmed_at 없음 + 열려 있음), 최신순"""
        evs = [e for e in self.open_events() if not e["confirmed_at"]]
        return sorted(evs, key=lambda e: -e["detected_at"])

    def student_name(self, seat: int) -> str:
        s = self.seats.get(seat)
        return s["student_name"] if s and s["reg_id"] else ""

    def setting(self, key: str, default: str = "") -> str:
        return self.settings.get(key, default)

    def alert_sound_on(self) -> bool:
        """app_setting alert_sound (기본 켜짐). 끄는 값은 여기 한 곳에서만 정한다."""
        return self.setting("alert_sound", "1").strip().lower() not in ("0", "off", "false")
