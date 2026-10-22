"""
SQLite 기록. DB 에 쓰는 곳은 여기 한 곳이다 (hub 프로세스 하나만 쓴다).
WAL 모드로 열어, 다른 프로그램(DB 뷰어)이 읽는 동안에도 기록이 막히지 않게 한다.

스키마 원본은 service/jarvis_DB/create.sql (시작할 때 실행). Confluence "Data Structure" v2 기준.
- seat              : 좌석 1~4 (label, 2×2 옆자리, 학생 노트북 node, 프리셋 등록 여부, 지금 앉은 학생)
- seat_registration : 자리 등록 이력 (이름, 바른 자세 기준값, 영역)
- device            : 장비 (TCP 노드 + 가상 장비 2·21·22), 시설 관리 탭
- device_log        : 장비 연결 이력
- help_event        : 도움 이벤트 (event_manager 가 정한 상태)
- event_log         : 이벤트별 처리 기록
- event_clip        : 이벤트 녹화 (팬틸트 cam2)
- class_condition   : 반 평균 (10초마다) + 세부 6개 비율 (drowsy_ratio ...)
- app_setting       : 강사 화면 [설정]

DB 에는 Interface Spec 의 숫자 코드로 저장한다. event_manager 는 지금처럼 문자열
("fall", "active", "instructor_console" …)로 다루고, 바꾸는 일은 여기서만 한다
(저장할 때 문자열 → 숫자, 읽을 때 숫자 → 문자열).

스키마를 바꾸면 SCHEMA_VERSION 을 올린다. 옛 hub.db 는 지우고 다시 만든다 (마이그레이션 안 함).
외래 키는 seat ↔ device, seat ↔ seat_registration 이 서로 참조하므로 PRAGMA foreign_keys 는 켜지 않는다.
"""

import json
import sqlite3
from pathlib import Path

from common.protocol import CONDITION_DETAIL_KEYS
from hub.control_config import CREATE_SQL

SCHEMA_VERSION = 4          # 1 = cameras/seats, 2 = nodes/messages/node_status, 3 = Data Structure v2,
                            # 4 = class_condition 세부 6개 (drowsy_ratio ...)

# ---- Interface Spec 숫자 코드 ----------------------------------------------
KIND = {"fall": 1, "question": 2, "away": 3}
STATUS = {"active": 1, "waiting": 2, "closed": 3}
ALERT_STAGE = {"none": 0, "neighbor": 1, "all": 2, "long": 3}
CLOSE_REASON = {None: 0, "resolved": 1, "false_alarm": 2, "expired": 3, "cleared": 4}
EXPLAIN_MODE = {None: 0, "gemini": 1, "ollama": 2, "default": 3}

CLIP_RECORDING, CLIP_SAVED, CLIP_DELETED = 1, 2, 3
CAM2_NODE = 22
DAY_S = 86400.0

# 장비 번호 (device.node, event_log.actor, help_event.confirmed_by)
SYSTEM_NODE = 0
NODE_CODE = {"system": 0, "control": 0, "vision_service": 1, "camera_controller": 2,
             "instructor_console": 3}
STUDENT_BASE = 10           # student_client_N -> 10 + N (11~14)
CAM1_NODE = 21

# 장비 종류 (device.kind)
DEV_SERVER, DEV_CAMERA, DEV_PANTILT, DEV_STUDENT, DEV_CONSOLE = 1, 2, 3, 4, 5

# 처음 만들 때 넣는 장비 행 (node, name, kind, seat). 이름·위치는 시설 관리 탭에서 바꿀 수 있다.
DEFAULT_DEVICES = (
    (0, "Control 서버", DEV_SERVER, None),
    (1, "Vision Service", DEV_SERVER, None),
    (2, "팬틸트", DEV_PANTILT, None),                 # Control 내부 모듈 (가상)
    (3, "강사 화면", DEV_CONSOLE, None),
    *((STUDENT_BASE + s, f"학생 노트북 {s}", DEV_STUDENT, s) for s in range(1, 5)),
    (CAM1_NODE, "고정 카메라 cam1", DEV_CAMERA, None),  # 가상 (UDP 송출만)
    (CAM2_NODE, "팬틸트 카메라 cam2", DEV_CAMERA, None),  # 가상
)


def node_code(name) -> int | None:
    """노드 이름 -> 장비 번호. "student_client_3" -> 13. 이미 숫자면 그대로."""
    if name is None or isinstance(name, int):
        return name
    if name in NODE_CODE:
        return NODE_CODE[name]
    if name.startswith("student_client_"):
        return STUDENT_BASE + int(name.rsplit("_", 1)[1])
    raise ValueError(f"알 수 없는 노드: {name}")


def node_name(code: int | None) -> str | None:
    """장비 번호 -> 노드 이름 (0 은 "system")."""
    if code is None:
        return None
    if code == SYSTEM_NODE:
        return "system"
    if STUDENT_BASE < code <= STUDENT_BASE + 4:
        return f"student_client_{code - STUDENT_BASE}"
    for k, v in NODE_CODE.items():
        if v == code and k not in ("system", "control"):
            return k
    return str(code)


def _rev(table: dict) -> dict:
    return {v: k for k, v in table.items()}


_KIND_R, _STATUS_R = _rev(KIND), _rev(STATUS)
_ALERT_R, _CLOSE_R, _EXPLAIN_R = _rev(ALERT_STAGE), _rev(CLOSE_REASON), _rev(EXPLAIN_MODE)


def _json(v):
    return None if v is None else json.dumps(v, ensure_ascii=False)


class SchemaMismatch(RuntimeError):
    pass


class Database:
    def __init__(self, path, create_sql: Path = CREATE_SQL):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._check_version()
        self.conn.executescript(Path(create_sql).read_text(encoding="utf-8"))
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        self.conn.executemany(
            "INSERT OR IGNORE INTO device (node, name, kind, seat) VALUES (?,?,?,?)",
            DEFAULT_DEVICES)
        self.conn.commit()

    def _check_version(self):
        has_tables = self.conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if has_tables and version != SCHEMA_VERSION:
            self.conn.close()
            raise SchemaMismatch(
                f"{self.path} 는 옛 스키마(v{version})입니다. 지금은 v{SCHEMA_VERSION}. "
                f"개발 중에는 hub.db, hub.db-wal, hub.db-shm 을 지우고 다시 실행하세요.")

    # =====================================================================
    # 좌석
    # =====================================================================
    def sync_seats(self, seats: dict):
        """control.yaml 의 seats 를 seat 표에 반영 (label, neighbors, 학생 노트북 node).
        has_preset 은 각도(pan, tilt)가 있으면 1. presets.json 저장으로 1이 된 값은 내리지 않는다."""
        self.conn.executemany(
            "INSERT INTO seat (seat, label, neighbors, device_node, has_preset) VALUES (?,?,?,?,?)"
            " ON CONFLICT(seat) DO UPDATE SET label=excluded.label, neighbors=excluded.neighbors,"
            " device_node=excluded.device_node,"
            " has_preset=max(seat.has_preset, excluded.has_preset)",
            [(int(s), c.get("label", f"{s}번 좌석"),
              ",".join(str(n) for n in c.get("neighbors", [])),
              STUDENT_BASE + int(s),
              int(c.get("pan") is not None and c.get("tilt") is not None))
             for s, c in seats.items()])
        self.conn.commit()

    def set_has_preset(self, seat: int, has: bool = True):
        """presets.json 에 좌석 각도를 저장했을 때."""
        self.conn.execute("UPDATE seat SET has_preset=? WHERE seat=?", (int(has), seat))
        self.conn.commit()

    def seat_info(self) -> list[dict]:
        """좌석도: 좌석 + 지금 앉은 학생 이름."""
        rows = self.conn.execute(
            "SELECT s.seat, s.label, s.neighbors, s.device_node, s.has_preset, s.reg_id,"
            " r.student_name, r.registered_at"
            " FROM seat s LEFT JOIN seat_registration r ON r.reg_id = s.reg_id"
            " ORDER BY s.seat").fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # 자리 등록
    # =====================================================================
    def register_seat(self, seat: int, student_name: str, baseline: dict,
                      zones, ts: float) -> int:
        """[자리 등록]. 이전 등록에 replaced_at 을 찍고, 새 행을 seat.reg_id 로 건다."""
        with self.conn:
            self.conn.execute(
                "UPDATE seat_registration SET replaced_at=? WHERE seat=? AND replaced_at IS NULL",
                (ts, seat))
            cur = self.conn.execute(
                "INSERT INTO seat_registration (seat, student_name, baseline, zones, registered_at)"
                " VALUES (?,?,?,?,?)",
                (seat, student_name, _json(baseline), _json(zones), ts))
            reg_id = cur.lastrowid
            self.conn.execute("UPDATE seat SET reg_id=? WHERE seat=?", (reg_id, seat))
        return reg_id

    def current_registration(self, seat: int) -> dict | None:
        """학생 노트북 시작 시 기준값 (baseline_request). 없으면 None."""
        row = self.conn.execute(
            "SELECT * FROM seat_registration WHERE seat=? AND replaced_at IS NULL"
            " ORDER BY reg_id DESC LIMIT 1", (seat,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["baseline"] = json.loads(d["baseline"]) if d["baseline"] else None
        d["zones"] = json.loads(d["zones"]) if d["zones"] else None
        return d

    # =====================================================================
    # 장비
    # =====================================================================
    def reset_devices(self, now: float):
        """시작할 때: control 만 online, 나머지는 접속·하트비트가 오면 online."""
        with self.conn:
            self.conn.execute("UPDATE device SET online=0 WHERE node != ?", (SYSTEM_NODE,))
            self.conn.execute("UPDATE device SET online=1, last_seen=?, note=NULL WHERE node=?",
                              (now, SYSTEM_NODE))
            self.conn.execute("INSERT INTO device_log (node, ts, online, note) VALUES (?,?,1,?)",
                              (SYSTEM_NODE, now, "시작"))

    def set_device_online(self, node, online: bool, ts: float, note: str | None = None):
        """device_update 와 같은 내용: device 갱신 + device_log 한 줄."""
        code = node_code(node)
        with self.conn:
            self.conn.execute(
                "UPDATE device SET online=?, note=?,"
                " last_seen=CASE WHEN ? THEN ? ELSE last_seen END WHERE node=?",
                (int(online), note or None, int(online), ts, code))
            self.conn.execute("INSERT INTO device_log (node, ts, online, note) VALUES (?,?,?,?)",
                              (code, ts, int(online), note or None))

    def touch_device(self, node, ts: float, value: dict | None = None):
        """heartbeat 수신: last_seen (Control 시계). 학생 heartbeat 면 rtt_ms·cam_ok 도."""
        v = value or {}
        cam_ok = v.get("cam_ok")
        self.conn.execute(
            "UPDATE device SET last_seen=?, net_rtt_ms=coalesce(?, net_rtt_ms),"
            " cam_ok=coalesce(?, cam_ok) WHERE node=?",
            (ts, v.get("rtt_ms"), None if cam_ok is None else int(bool(cam_ok)), node_code(node)))
        # vision_service heartbeat 의 카메라 fps -> 가상 장비 21·22 (프레임 수신 = online)
        for key, cam in (("cam1_fps", CAM1_NODE), ("cam2_fps", CAM2_NODE)):
            if key not in v:
                continue
            online = int((v.get(key) or 0) > 0)
            row = self.conn.execute("SELECT online FROM device WHERE node=?", (cam,)).fetchone()
            if online:
                self.conn.execute("UPDATE device SET last_seen=? WHERE node=?", (ts, cam))
            if row is not None and row["online"] != online:
                note = None if online else "영상 끊김"
                self.conn.execute("UPDATE device SET online=?, note=? WHERE node=?",
                                  (online, note, cam))
                self.conn.execute(
                    "INSERT INTO device_log (node, ts, online, note) VALUES (?,?,?,?)",
                    (cam, ts, online, note))
        self.conn.commit()

    def devices(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM device ORDER BY node")]

    # =====================================================================
    # 이벤트
    # =====================================================================
    def save_event(self, row: dict):
        """event_manager 의 help_event 행(문자열 값)을 숫자 코드로 바꿔 저장.
        reg_id 는 처음 저장할 때 그 좌석에 앉아 있던 학생으로 정하고, 이후에는 바꾸지 않는다."""
        self.conn.execute(
            "INSERT INTO help_event (event_id, seat, reg_id, kind, priority, status, alert_stage,"
            " close_reason, hand_down, detected_at, confirmed_at, confirmed_by, closed_at, gen,"
            " explain_text, explain_mode)"
            " VALUES (?,?,(SELECT reg_id FROM seat WHERE seat=?),?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(event_id) DO UPDATE SET status=excluded.status,"
            " priority=excluded.priority, alert_stage=excluded.alert_stage,"
            " close_reason=excluded.close_reason, hand_down=excluded.hand_down,"
            " confirmed_at=excluded.confirmed_at, confirmed_by=excluded.confirmed_by,"
            " closed_at=excluded.closed_at, gen=coalesce(excluded.gen, help_event.gen),"
            " explain_text=excluded.explain_text, explain_mode=excluded.explain_mode",
            (row["event_id"], row["seat"], row["seat"],
             KIND[row["kind"]], row["priority"], STATUS[row["status"]],
             ALERT_STAGE[row.get("alert_stage") or "none"],
             CLOSE_REASON[row.get("close_reason")],
             int(bool(row.get("hand_down"))),
             row["detected_at"], row.get("confirmed_at"), node_code(row.get("confirmed_by")),
             row.get("closed_at"), row.get("gen"),
             row.get("explain_text"), EXPLAIN_MODE[row.get("explain_mode")]))

    def insert_log(self, event_id: int, ts: float, action: str, actor, detail=None):
        self.conn.execute(
            "INSERT INTO event_log (event_id, ts, action, actor, detail) VALUES (?,?,?,?,?)",
            (event_id, ts, action, node_code(actor), _json(detail)))

    def apply(self, saves, logs):
        """event_manager 의 Effects 를 한 번에 기록 (한 트랜잭션)."""
        if not saves and not logs:
            return
        with self.conn:
            for row in saves:
                self.save_event(row)
            for log_row in logs:
                self.insert_log(*log_row)

    def max_event_id(self) -> int:
        row = self.conn.execute("SELECT max(event_id) FROM help_event").fetchone()
        return row[0] or 0

    @staticmethod
    def _decode_event(r: sqlite3.Row) -> dict:
        """help_event 행 -> event_manager.HelpEvent 인자 (문자열 값)."""
        return {
            "event_id": r["event_id"], "seat": r["seat"],
            "kind": _KIND_R[r["kind"]], "priority": r["priority"],
            "status": _STATUS_R[r["status"]], "detected_at": r["detected_at"],
            "confirmed_at": r["confirmed_at"], "confirmed_by": node_name(r["confirmed_by"]),
            "closed_at": r["closed_at"], "close_reason": _CLOSE_R.get(r["close_reason"]),
            "alert_stage": _ALERT_R.get(r["alert_stage"], "none"),
            "explain_text": r["explain_text"], "explain_mode": _EXPLAIN_R.get(r["explain_mode"]),
            "hand_down": bool(r["hand_down"]),
        }

    def load_open_events(self) -> list[dict]:
        """재시작 전에 열려 있던 이벤트 (active / waiting)."""
        rows = self.conn.execute(
            "SELECT * FROM help_event WHERE status != ? ORDER BY event_id",
            (STATUS["closed"],)).fetchall()
        return [self._decode_event(r) for r in rows]

    def unconfirmed_count(self) -> int:
        """세로 탭 미확인 배지."""
        return self.conn.execute(
            "SELECT count(*) FROM help_event WHERE confirmed_at IS NULL AND status != ?",
            (STATUS["closed"],)).fetchone()[0]

    def search_events(self, start: float | None = None, end: float | None = None,
                      kind: int | None = None, student: str | None = None,
                      limit: int = 200) -> list[dict]:
        """로그 검색 (log_query): 기간·종류(숫자 코드)·학생 이름(부분 일치). 최신 순, 숫자 코드 그대로."""
        where, args = [], []
        if start is not None:
            where.append("e.detected_at >= ?"); args.append(start)
        if end is not None:
            where.append("e.detected_at < ?"); args.append(end)
        if kind is not None:
            where.append("e.kind = ?"); args.append(kind)
        if student:
            where.append("r.student_name LIKE ?"); args.append(f"%{student}%")
        sql = ("SELECT e.*, r.student_name FROM help_event e"
               " LEFT JOIN seat_registration r ON r.reg_id = e.reg_id"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY e.detected_at DESC LIMIT ?")
        return [dict(r) for r in self.conn.execute(sql, (*args, limit))]

    def event_logs(self, event_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM event_log WHERE event_id=? ORDER BY log_id", (event_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d["detail"] else None
            out.append(d)
        return out

    # =====================================================================
    # 녹화
    # =====================================================================
    def start_clip(self, event_id: int, path: str, ts: float, camera: int = CAM2_NODE) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO event_clip (event_id, camera, path, started_at, status)"
                " VALUES (?,?,?,?,?)", (event_id, camera, path, ts, CLIP_RECORDING))
        return cur.lastrowid

    def end_clip(self, clip_id: int, ts: float, keep_days: float):
        with self.conn:
            self.conn.execute(
                "UPDATE event_clip SET ended_at=?, status=?, keep_until=? WHERE clip_id=?",
                (ts, CLIP_SAVED, ts + keep_days * DAY_S, clip_id))

    def close_recording_clips(self, ts: float, keep_days: float) -> int:
        """재시작 시 녹화 중이던 클립을 저장됨(2)으로 닫는다."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE event_clip SET ended_at=?, status=?, keep_until=? WHERE status=?",
                (ts, CLIP_SAVED, ts + keep_days * DAY_S, CLIP_RECORDING))
        return cur.rowcount

    def discard_clip(self, clip_id: int, ts: float):
        """프레임을 한 장도 못 쓴 녹화 (cam2 영상 없음). 파일은 없고 삭제됨(3)으로 남긴다."""
        with self.conn:
            self.conn.execute("UPDATE event_clip SET ended_at=?, status=? WHERE clip_id=?",
                              (ts, CLIP_DELETED, clip_id))

    def latest_clip(self, event_id: int) -> dict | None:
        """이벤트 창 다시 보기 (clip_request): 녹화 중인 것 > 마지막 저장본 > 마지막 행 순서."""
        rows = self.clips_for_event(event_id)
        for st in (CLIP_RECORDING, CLIP_SAVED):
            hit = [r for r in rows if r["status"] == st]
            if hit:
                return hit[-1]
        return rows[-1] if rows else None

    def expired_clips(self, now: float) -> list[dict]:
        """보관 기간이 지난 클립 (파일 삭제 대상)."""
        rows = self.conn.execute(
            "SELECT * FROM event_clip WHERE status=? AND keep_until IS NOT NULL AND keep_until < ?",
            (CLIP_SAVED, now)).fetchall()
        return [dict(r) for r in rows]

    def mark_clip_deleted(self, clip_id: int):
        with self.conn:
            self.conn.execute("UPDATE event_clip SET status=? WHERE clip_id=?",
                              (CLIP_DELETED, clip_id))

    def clips_for_event(self, event_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM event_clip WHERE event_id=? ORDER BY clip_id", (event_id,)).fetchall()
        return [dict(r) for r in rows]

    # =====================================================================
    # 반 평균 · 설정
    # =====================================================================
    def insert_class_condition(self, ts: float, focus_low_ratio: float,
                               posture_bad_ratio: float, n: int, detail: dict | None = None):
        """detail = {drowsy: 0.25 | None, ...} (CONDITION_DETAIL_KEYS). 없는 키는 NULL."""
        detail = detail or {}
        cols = [f"{k}_ratio" for k in CONDITION_DETAIL_KEYS]
        vals = [detail.get(k) for k in CONDITION_DETAIL_KEYS]
        with self.conn:
            self.conn.execute(
                "INSERT INTO class_condition (ts, focus_low_ratio, posture_bad_ratio, n, "
                + ", ".join(cols) + ") VALUES (?,?,?,?" + ",?" * len(cols) + ")",
                (ts, focus_low_ratio, posture_bad_ratio, n, *vals))

    def get_setting(self, key: str, default=None):
        """app_setting 값 (JSON 으로 저장). 없으면 default."""
        row = self.conn.execute("SELECT value FROM app_setting WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def get_settings(self) -> dict:
        return {r["key"]: json.loads(r["value"])
                for r in self.conn.execute("SELECT key, value FROM app_setting")}

    def set_setting(self, key: str, value, ts: float):
        with self.conn:
            self.conn.execute(
                "INSERT INTO app_setting (key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), ts))

    def close(self):
        self.conn.close()
