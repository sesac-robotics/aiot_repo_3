"""
SQLite 기록. DB 에 쓰는 곳은 여기 한 곳이다 (hub 프로세스 하나만 쓴다).
WAL 모드로 열어, 다른 프로그램(DB 뷰어)이 읽는 동안에도 기록이 막히지 않게 한다.

스키마 원본은 db/create.sql (시작할 때 실행).
- nodes       : 노드별 접속 PC (호스트 이름, IP, OS)
- messages    : 노드가 보낸 원본 메시지 (heartbeat, track_offset 제외)
- node_status : 노드별 최신 하트비트 (fps, cpu, cam1_fps, cam2_fps) + online
- seat        : 좌석 프리셋 각도·옆자리 (config/control.yaml 에서 채움)
- help_event  : 도움 이벤트 (event_manager 가 정한 상태)
- event_log   : 이벤트별 처리 기록

스키마를 바꾸면 SCHEMA_VERSION 을 올린다. 옛 hub.db 는 지우고 다시 만든다 (마이그레이션 안 함).
"""

import json
import sqlite3
from pathlib import Path

from common.config import CREATE_SQL

SCHEMA_VERSION = 2          # 1 = cameras/seats 가 있던 옛 구조


class SchemaMismatch(RuntimeError):
    pass


class Database:
    def __init__(self, path, create_sql: Path = CREATE_SQL):
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._check_version()
        self.conn.executescript(Path(create_sql).read_text(encoding="utf-8"))
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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

    # ---- 노드 ------------------------------------------------------------
    def insert_message(self, msg: dict, rx_ts: float):
        self.conn.execute(
            "INSERT INTO messages (node, seq, type, seat, ts, rx_ts, value, conf)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (msg["node"], msg["seq"], msg["type"], msg.get("seat"), msg["ts"],
             rx_ts, json.dumps(msg["value"], ensure_ascii=False), msg["conf"]))
        self.conn.commit()

    def upsert_node_status(self, node: str, last_ts: float, value: dict):
        self.conn.execute(
            "INSERT INTO node_status (node, last_ts, fps, cpu, online, cam1_fps, cam2_fps)"
            " VALUES (?,?,?,?,1,?,?)"
            " ON CONFLICT(node) DO UPDATE SET last_ts=excluded.last_ts,"
            " fps=excluded.fps, cpu=excluded.cpu, online=1,"
            " cam1_fps=excluded.cam1_fps, cam2_fps=excluded.cam2_fps",
            (node, last_ts, value.get("fps"), value.get("cpu"),
             value.get("cam1_fps"), value.get("cam2_fps")))
        self.conn.commit()

    def set_node_online(self, node: str, online: bool):
        self.conn.execute(
            "INSERT INTO node_status (node, online) VALUES (?,?)"
            " ON CONFLICT(node) DO UPDATE SET online=excluded.online",
            (node, int(online)))
        self.conn.commit()

    def upsert_node_pc(self, node: str, pc: dict, peer: str, connected_ts: float, ts: float):
        """노드가 어느 PC 에서 접속했는지. peer = hub 가 본 상대 주소 (ip:port)."""
        self.conn.execute(
            "INSERT INTO nodes (node, hostname, ip, peer, os, python, pid, connected_ts, updated_ts)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(node) DO UPDATE SET hostname=excluded.hostname, ip=excluded.ip,"
            " peer=excluded.peer, os=excluded.os, python=excluded.python, pid=excluded.pid,"
            " connected_ts=excluded.connected_ts, updated_ts=excluded.updated_ts",
            (node, pc.get("hostname"), pc.get("ip"), peer, pc.get("os"), pc.get("python"),
             pc.get("pid"), connected_ts, ts))
        self.conn.commit()

    # ---- 좌석 ------------------------------------------------------------
    def sync_seats(self, seats: dict):
        """control.yaml 의 seats 를 seat 표에 반영."""
        self.conn.executemany(
            "INSERT INTO seat (seat, label, pan, tilt, neighbors) VALUES (?,?,?,?,?)"
            " ON CONFLICT(seat) DO UPDATE SET label=excluded.label, pan=excluded.pan,"
            " tilt=excluded.tilt, neighbors=excluded.neighbors",
            [(int(s), c.get("label", f"{s}번 좌석"), c.get("pan"), c.get("tilt"),
              ",".join(str(n) for n in c.get("neighbors", [])))
             for s, c in seats.items()])
        self.conn.commit()

    # ---- 이벤트 ----------------------------------------------------------
    def save_event(self, row: dict):
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "event_id")
        self.conn.execute(
            f"INSERT INTO help_event ({cols}) VALUES ({marks})"
            f" ON CONFLICT(event_id) DO UPDATE SET {updates}",
            tuple(row.values()))

    def insert_log(self, event_id: int, ts: float, action: str, actor: str, detail=None):
        self.conn.execute(
            "INSERT INTO event_log (event_id, ts, action, actor, detail) VALUES (?,?,?,?,?)",
            (event_id, ts, action, actor,
             None if detail is None else json.dumps(detail, ensure_ascii=False)))

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

    def load_open_events(self) -> list[dict]:
        """재시작 전에 열려 있던 이벤트 (active / waiting)."""
        rows = self.conn.execute(
            "SELECT * FROM help_event WHERE status != 'closed' ORDER BY event_id").fetchall()
        return [dict(r) for r in rows]

    def close(self):
        self.conn.close()
