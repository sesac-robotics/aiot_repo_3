"""
SQLite 기록. DB는 hub 프로세스 하나만 쓴다.
WAL 모드로 열어, 다른 프로그램(show_db, DB 뷰어)이 읽는 동안에도 기록이 막히지 않게 한다.

지금 있는 표 (TCP·DB 시험용 최소 구성):
- messages    : 노드가 보낸 원본 메시지 전부
- node_status : 노드별 최신 하트비트 (fps, cpu)
- cameras     : 노드별 카메라 정보 (node_info 최신값)
- seats       : 좌석과 그 좌석을 보는 노드 (node_info 최신값)
"""

import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY, node TEXT, seq INTEGER, type TEXT, seat INTEGER,
  ts REAL, rx_ts REAL, value TEXT, conf REAL);

CREATE TABLE IF NOT EXISTS node_status (
  node TEXT PRIMARY KEY, last_ts REAL, fps REAL, cpu REAL, clock_offset_s REAL);

CREATE TABLE IF NOT EXISTS cameras (
  node TEXT PRIMARY KEY, cam_index INTEGER, width INTEGER, height INTEGER,
  fps REAL, updated_ts REAL);

CREATE TABLE IF NOT EXISTS seats (
  seat INTEGER, node TEXT, label TEXT, updated_ts REAL,
  PRIMARY KEY (seat, node));
"""


class Database:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def insert_message(self, msg: dict, rx_ts: float):
        self.conn.execute(
            "INSERT INTO messages (node, seq, type, seat, ts, rx_ts, value, conf)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (msg["node"], msg["seq"], msg["type"], msg.get("seat"), msg["ts"],
             rx_ts, json.dumps(msg["value"], ensure_ascii=False), msg["conf"]))
        self.conn.commit()

    def upsert_node_status(self, node: str, last_ts: float, value: dict):
        self.conn.execute(
            "INSERT INTO node_status (node, last_ts, fps, cpu) VALUES (?,?,?,?)"
            " ON CONFLICT(node) DO UPDATE SET last_ts=excluded.last_ts,"
            " fps=excluded.fps, cpu=excluded.cpu",
            (node, last_ts, value.get("fps"), value.get("cpu")))
        self.conn.commit()

    def upsert_node_info(self, node: str, ts: float, value: dict):
        """node_info 한 건을 cameras / seats 에 반영. 그 노드의 예전 좌석 목록은 지운다."""
        cam = value.get("camera") or {}
        self.conn.execute(
            "INSERT INTO cameras (node, cam_index, width, height, fps, updated_ts)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(node) DO UPDATE SET cam_index=excluded.cam_index,"
            " width=excluded.width, height=excluded.height, fps=excluded.fps,"
            " updated_ts=excluded.updated_ts",
            (node, cam.get("index"), cam.get("width"), cam.get("height"),
             cam.get("fps"), ts))
        self.conn.execute("DELETE FROM seats WHERE node=?", (node,))
        self.conn.executemany(
            "INSERT INTO seats (seat, node, label, updated_ts) VALUES (?,?,?,?)",
            [(s["seat"], node, s.get("label"), ts) for s in value.get("seats", [])])
        self.conn.commit()

    def close(self):
        self.conn.close()
