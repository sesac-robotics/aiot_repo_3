"""
[질의] 강사 자연어 질의의 도구(tool) 실행. 읽기 전용.

vision_service 의 LLM 이 tool_call {name, args} 를 보내면 hub 가 AskTools.run() 을 불러 결과를 tool_result 로 돌려준다.
- 판단하지 않는다 (event_manager 의 일). DB 에 쓰지 않는다 (db.py 의 일). SELECT 만 한다.
- LLM 이 SQL 을 만들지 않는다. 미리 정한 함수만 있고, LLM 은 인자만 고른다. 인자는 여기서 다시 검사한다.
- 학생 이름은 내보내지 않는다 (10/22 결정: 좌석 번호로만 답한다). seat_registration 은 읽지 않는다.
- 어떤 오류도 밖으로 던지지 않고 {"error": "..."} 로 돌려준다 (LLM 이 보고 다시 시도하거나 사과한다).
- 시각은 control PC 의 지역 시간 "YYYY-MM-DD HH:MM:SS" 로 주고받는다.

도구 (설명·인자 정의는 vision_service/ask.py 의 TOOL_DECLS 와 같아야 한다):
    get_open_events()                                          지금 열린 이벤트·대기열 (EventManager 상태)
    search_events(kind, seat, since, until, status, limit)     지난 이벤트 검색 (help_event)
    get_event_timeline(event_id)                               이벤트 한 건의 처리 과정 (event_log)
    get_stats(since, until)                                    종류·결과·좌석별 건수, 확인·처리 시간
    get_device_status()                                        장비 연결 상태 (device)
    get_class_condition(minutes)                               반 평균 집중·자세 (class_condition, 개인 없음)
"""

import logging
import time
from datetime import datetime

from hub.db import ALERT_STAGE, CLOSE_REASON, KIND, STATUS, node_name

log = logging.getLogger("ask_tools")

_KIND_R = {v: k for k, v in KIND.items()}
_STATUS_R = {v: k for k, v in STATUS.items()}
_CLOSE_R = {v: k for k, v in CLOSE_REASON.items()}
_ALERT_R = {v: k for k, v in ALERT_STAGE.items()}

MAX_LIMIT = 50
STATUS_FILTERS = ("open", "closed", "unconfirmed", "resolved", "false_alarm", "expired", "cleared")


class ToolError(Exception):
    """인자가 틀림 등. 메시지가 그대로 LLM 에 간다."""


def fmt(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else None


def ago(ts: float | None, now: float) -> str | None:
    """사람이 읽는 '방금 / 3분 전 / 2시간 전 / 1일 전'."""
    if not ts:
        return None
    s = max(0.0, now - ts)
    if s < 60:
        return "방금"
    if s < 3600:
        return f"{int(s // 60)}분 전"
    if s < 86400:
        return f"{int(s // 3600)}시간 전"
    return f"{int(s // 86400)}일 전"


def dur(s: float | None) -> str | None:
    if s is None:
        return None
    s = int(round(s))
    return f"{s // 60}분 {s % 60}초" if s >= 60 else f"{s}초"


def parse_time(value, now: float, name: str) -> float | None:
    """'2026-10-22 10:30', '2026-10-22T10:30:00', '10:30'(오늘), 'today'/'오늘', 'yesterday'/'어제', 'now'/'지금' -> epoch.
    작은 모델이 자주 하는 실수를 받아 준다: until='today' 는 오늘 끝(내일 0시), until='yesterday' 는 오늘 0시."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ToolError(f"{name} 는 'YYYY-MM-DD HH:MM' 형식 문자열")
    v = value.strip().replace("T", " ")
    today = datetime.fromtimestamp(now).date()
    midnight = datetime.combine(today, datetime.min.time()).timestamp()
    word = v.lower()
    if word in ("now", "지금", "현재"):
        return now
    if word in ("today", "오늘"):
        return midnight if name != "until" else midnight + 86400
    if word in ("yesterday", "어제"):
        return midnight - 86400 if name != "until" else midnight
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v, f).timestamp()
        except ValueError:
            pass
    for f in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(v, f).time()
            return datetime.combine(today, t).timestamp()
        except ValueError:
            pass
    raise ToolError(f"{name} 형식을 모름: {value!r} ('YYYY-MM-DD HH:MM' 으로)")


def _seat(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        seat = int(value)
    except (TypeError, ValueError):
        raise ToolError(f"seat 는 1~4 숫자: {value!r}") from None
    if seat not in (1, 2, 3, 4):
        raise ToolError(f"seat 는 1~4: {seat}")
    return seat


def _kind(value) -> str | None:
    if value in (None, "", "all"):
        return None
    if value not in KIND:
        raise ToolError(f"kind 는 fall / question / away 중 하나: {value!r}")
    return value


def _round(x, nd=1):
    return round(x, nd) if x is not None else None


class AskTools:
    def __init__(self, db, events, clock=time.time):
        """db = hub.db.Database (conn 으로 SELECT 만), events = EventManager, clock = 지금 시각 함수."""
        self.db = db
        self.events = events
        self.clock = clock
        self.tools = {
            "get_open_events": self.get_open_events,
            "search_events": self.search_events,
            "get_event_timeline": self.get_event_timeline,
            "get_stats": self.get_stats,
            "get_device_status": self.get_device_status,
            "get_class_condition": self.get_class_condition,
        }

    def run(self, name: str, args: dict | None) -> dict:
        """절대 예외를 던지지 않는다."""
        fn = self.tools.get(name)
        if fn is None:
            return {"error": f"모르는 도구: {name}. 쓸 수 있는 도구: {', '.join(self.tools)}"}
        args = args if isinstance(args, dict) else {}
        try:
            return fn(**args)
        except TypeError as e:                   # 모르는 인자 이름
            return {"error": f"{name} 인자 오류: {e}"}
        except ToolError as e:
            return {"error": str(e)}
        except Exception as e:                   # 버그 — 서버는 계속
            log.exception("도구 %s 실행 오류 args=%s", name, args)
            return {"error": f"{name} 실행 중 오류: {type(e).__name__}"}

    # ------------------------------------------------------------------ 행 변환
    def _event_row(self, r) -> dict:
        d = dict(r)
        det, conf, closed = d["detected_at"], d["confirmed_at"], d["closed_at"]
        return {
            "event_id": d["event_id"], "seat": d["seat"], "kind": _KIND_R.get(d["kind"]),
            "status": _STATUS_R.get(d["status"]),
            "close_reason": _CLOSE_R.get(d["close_reason"]),
            "detected_at": fmt(det), "confirmed_at": fmt(conf), "closed_at": fmt(closed),
            "confirm_after_s": _round(conf - det) if conf else None,      # 감지 → 강사 확인까지
            "close_after_s": _round(closed - det) if closed else None,    # 감지 → 종료까지
            "confirm_after": dur(conf - det) if conf else None,
            "close_after": dur(closed - det) if closed else None,
            "alert_stage": _ALERT_R.get(d["alert_stage"], "none"),
            "hand_down": bool(d["hand_down"]),
            "explain_text": d["explain_text"],
        }

    def _where(self, kind=None, seat=None, since=None, until=None, status=None):
        now = self.clock()
        where, args = [], []
        kind, seat = _kind(kind), _seat(seat)
        start, end = parse_time(since, now, "since"), parse_time(until, now, "until")
        if kind:
            where.append("kind = ?"); args.append(KIND[kind])
        if seat:
            where.append("seat = ?"); args.append(seat)
        if start is not None:
            where.append("detected_at >= ?"); args.append(start)
        if end is not None:
            where.append("detected_at < ?"); args.append(end)
        if status not in (None, "", "all"):
            if status not in STATUS_FILTERS:
                raise ToolError(f"status 는 {' / '.join(STATUS_FILTERS)} 중 하나: {status!r}")
            if status == "open":
                where.append("status != ?"); args.append(STATUS["closed"])
            elif status == "closed":
                where.append("status = ?"); args.append(STATUS["closed"])
            elif status == "unconfirmed":
                where.append("confirmed_at IS NULL")
            else:
                where.append("close_reason = ?"); args.append(CLOSE_REASON[status])
        if start is not None and start > now + 60:
            raise ToolError(f"since({fmt(start)})가 현재 시각({fmt(now)})보다 뒤라 결과가 없다. "
                            "'오늘'이면 since='today', '최근 N분'이면 현재 시각에서 N분을 뺀 시각을 쓴다")
        if start is not None and end is not None and start >= end:
            raise ToolError(f"since({fmt(start)})가 until({fmt(end)})보다 늦다")
        cond = (" WHERE " + " AND ".join(where)) if where else ""
        rng = {"since": fmt(start), "until": fmt(end)}
        return cond, args, rng

    # ------------------------------------------------------------------ 도구
    def get_open_events(self) -> dict:
        now = self.clock()
        snap = self.events.snapshot()
        events = []
        for e in snap["events"]:
            events.append({
                "event_id": e["event_id"], "seat": e["seat"], "kind": e["kind"], "status": e["status"],
                "detected_at": fmt(e["detected_at"]), "elapsed": dur(now - e["detected_at"]),
                "confirmed": e["confirmed_at"] is not None, "alert_stage": e["alert_stage"],
                "hand_down": e["hand_down"], "risen": e.get("risen", False), "overdue": e.get("overdue", False),
                "explain_text": e.get("explain_text"),
            })
        q = snap["queue"]
        out = {"now": fmt(now), "count": len(events), "events": events,
               "tracking_event_id": q["active"], "waiting_order": q["waiting"]}
        if not events:
            out["note"] = "지금 열린 이벤트 없음"
        return out

    def search_events(self, kind=None, seat=None, since=None, until=None, status=None, limit=20) -> dict:
        try:
            limit = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            raise ToolError(f"limit 는 1~{MAX_LIMIT} 숫자") from None
        cond, args, rng = self._where(kind, seat, since, until, status)
        total = self.db.conn.execute(f"SELECT count(*) FROM help_event{cond}", args).fetchone()[0]
        rows = self.db.conn.execute(
            f"SELECT * FROM help_event{cond} ORDER BY detected_at DESC LIMIT ?", (*args, limit)).fetchall()
        out = {"count": total, "returned": len(rows), "truncated": total > len(rows), **rng,
               "filters": {k: v for k, v in (("kind", kind), ("seat", seat), ("status", status)) if v not in (None, "")},
               "events": [self._event_row(r) for r in rows]}
        if total == 0:
            out["note"] = "조건에 맞는 기록이 없음. 0건이라고 답한다 (지어내지 않는다)"
        return out

    def get_event_timeline(self, event_id=None) -> dict:
        try:
            event_id = int(event_id)
        except (TypeError, ValueError):
            raise ToolError("event_id 는 숫자") from None
        r = self.db.conn.execute("SELECT * FROM help_event WHERE event_id = ?", (event_id,)).fetchone()
        if r is None:
            return {"error": f"이벤트 {event_id} 없음"}
        logs = []
        for l in self.db.event_logs(event_id):
            detail = l.get("detail")
            if isinstance(detail, dict):
                detail = {k: v for k, v in detail.items() if k != "source"}   # 내부 정보는 빼고
            logs.append({"at": fmt(l["ts"]), "action": l["action"],
                         "by": node_name(l["actor"]) or "system", "detail": detail or None})
        return {"event": self._event_row(r), "timeline": logs}

    def get_stats(self, since=None, until=None) -> dict:
        cond, args, rng = self._where(since=since, until=until)
        rows = [self._event_row(r) for r in self.db.conn.execute(f"SELECT * FROM help_event{cond}", args)]
        by_kind = {}
        for k in KIND:
            ev = [e for e in rows if e["kind"] == k]
            conf = [e["confirm_after_s"] for e in ev if e["confirm_after_s"] is not None]
            res = [e["close_after_s"] for e in ev if e["close_reason"] == "resolved"]
            by_kind[k] = {
                "count": len(ev),
                "avg_confirm_s": _round(sum(conf) / len(conf)) if conf else None,
                "max_confirm_s": _round(max(conf)) if conf else None,
                "avg_resolve_s": _round(sum(res) / len(res)) if res else None,
            }
        by_result = {}
        for e in rows:
            key = e["close_reason"] or "open"
            by_result[key] = by_result.get(key, 0) + 1
        by_seat = {}
        for e in rows:
            by_seat[str(e["seat"])] = by_seat.get(str(e["seat"]), 0) + 1
        return {**rng, "total": len(rows), "by_kind": by_kind, "by_result": by_result, "by_seat": by_seat,
                "unconfirmed_open": sum(1 for e in rows if e["status"] != "closed" and not e["confirmed_at"])}

    def get_device_status(self) -> dict:
        now = self.clock()
        out = []
        for d in self.db.devices():
            item = {"name": d["name"], "online": bool(d["online"]),
                    "last_seen": ago(d["last_seen"], now) or "기록 없음"}
            if d["note"]:
                item["note"] = d["note"]
            if d["net_rtt_ms"] is not None and d["online"]:
                item["net_rtt_ms"] = _round(d["net_rtt_ms"])
            if d["cam_ok"] is not None and d["online"]:
                item["camera_ok"] = bool(d["cam_ok"])
            out.append(item)
        on = [d["name"] for d in out if d["online"]]
        off = [d["name"] for d in out if not d["online"]]
        return {"now": fmt(now), "online": on, "offline": off,
                "summary": f"연결 {len(on)}개, 끊김 {len(off)}개" + (f" ({', '.join(off)})" if off else ""),
                "devices": out}

    def get_class_condition(self, minutes=30) -> dict:
        try:
            minutes = max(1, min(240, int(minutes)))
        except (TypeError, ValueError):
            raise ToolError("minutes 는 1~240 숫자") from None
        now = self.clock()
        rows = self.db.conn.execute(
            "SELECT ts, focus_low_ratio, posture_bad_ratio, n FROM class_condition WHERE ts >= ? ORDER BY ts",
            (now - minutes * 60,)).fetchall()
        if not rows:
            return {"minutes": minutes, "samples": 0,
                    "note": "이 기간에 반 집계가 없음 (학생 노트북이 보고하지 않았거나 수업 전)"}
        f = [r["focus_low_ratio"] for r in rows]
        p = [r["posture_bad_ratio"] for r in rows]
        half = len(rows) // 2 or 1
        return {
            "minutes": minutes, "samples": len(rows), "from": fmt(rows[0]["ts"]), "to": fmt(rows[-1]["ts"]),
            "latest": {"focus_low_ratio": f[-1], "posture_bad_ratio": p[-1], "reporting": rows[-1]["n"]},
            "avg": {"focus_low_ratio": _round(sum(f) / len(f), 2), "posture_bad_ratio": _round(sum(p) / len(p), 2)},
            "first_half_avg_focus_low": _round(sum(f[:half]) / half, 2),
            "second_half_avg_focus_low": _round(sum(f[half:]) / max(1, len(f) - half), 2),
            "note": "비율은 반 전체 중 해당 학생 비율(0~1). 개인별 값은 없음",
        }
