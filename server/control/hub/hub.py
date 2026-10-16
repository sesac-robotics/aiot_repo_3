"""
hub TCP 서버 (TCP 통신 + DB 기록만 남긴 최소 구성)

- 노드 <-> hub: TCP 위의 줄 단위 JSON (common/message.py), 포트 5000
- 받은 메시지는 전부 messages 표에 기록
- heartbeat -> node_status, node_info -> nodes(접속 PC) / cameras / seats
- 같은 노드 이름이 다른 PC 에서 접속하면 경고 (설정 실수 찾기용)
- 사건 메시지(fall, hand_raise ...)에는 ack 로 답한다 (왕복 통신 확인용)
- 하트비트가 6초(3회) 안 오면 노드 끊김으로 로그

실행 (src 폴더에서):  python3 -m hub [--host 0.0.0.0] [--port 5000] [--db 경로] [-v]
"""

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass

from common.config import DB_PATH, load_config
from common.message import (DEFAULT_PORT, INCIDENT_PRIORITY, MessageError,
                            encode, make_message, parse_message)
from hub.db import Database

log = logging.getLogger("hub")

HEARTBEAT_TIMEOUT_S = 6.0          # 하트비트 2초 x 3회


@dataclass
class NodeConn:
    name: str
    writer: asyncio.StreamWriter
    last_heartbeat: float
    peer: str = ""                 # hub 가 본 상대 주소 "ip:port"
    connected_ts: float = 0.0
    hostname: str | None = None    # node_info 의 pc.hostname
    last_seq: int = -1
    online: bool = True


class HubServer:
    def __init__(self, host: str, port: int, db_path):
        self.host, self.port = host, port
        self.db_path = db_path
        self.db = Database(db_path)
        self.nodes: dict[str, NodeConn] = {}
        self.last_host: dict[str, str] = {}   # 노드 이름 -> 마지막으로 접속한 PC 호스트 이름
        self.seq = 0

    # ---- 송신 ----------------------------------------------------------
    async def send(self, node: str, type_: str, value: dict, seat=None) -> bool:
        conn = self.nodes.get(node)
        if not conn or not conn.online:
            log.warning("%s 미접속 - %s 전송 실패", node, type_)
            return False
        self.seq += 1
        msg = make_message("hub", self.seq, type_, seat, value)
        try:
            conn.writer.write(encode(msg))
            await conn.writer.drain()
            return True
        except (ConnectionError, OSError) as e:
            log.warning("%s 전송 오류: %s", node, e)
            return False

    # ---- 연결 처리 -----------------------------------------------------
    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("접속: %s", peer)
        node_name = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                rx_ts = time.time()
                try:
                    msg = parse_message(line)
                except MessageError as e:
                    log.warning("%s 잘못된 메시지: %s", peer, e)
                    continue

                # 첫 메시지의 node 이름으로 연결 등록 (같은 이름이 다시 오면 예전 연결을 끊음)
                if node_name is None:
                    node_name = msg["node"]
                    peer_str = f"{peer[0]}:{peer[1]}" if peer else "?"
                    old = self.nodes.get(node_name)
                    if old and old.writer is not writer:
                        if old.online:
                            log.warning("%s 가 이미 접속 중(%s, %s) - 새 연결(%s)로 바꿈. "
                                        "같은 이름을 두 PC에서 쓰고 있지 않은지 확인",
                                        node_name, old.hostname or "?", old.peer, peer_str)
                        old.writer.close()
                    self.nodes[node_name] = NodeConn(node_name, writer, rx_ts,
                                                     peer=peer_str, connected_ts=rx_ts)
                    log.info("노드 등록: %s (%s)", node_name, peer_str)

                await self.on_message(self.nodes[node_name], msg, rx_ts)
        except (ConnectionError, asyncio.IncompleteReadError) as e:
            log.info("%s 연결 오류: %s", peer, e)
        except Exception:
            log.exception("%s(%s) 메시지 처리 중 오류 - 연결을 끊음", node_name, peer)
        finally:
            conn = self.nodes.get(node_name) if node_name else None
            if conn and conn.writer is writer:
                conn.online = False
            writer.close()
            log.info("연결 종료: %s (%s)", node_name, peer)

    # ---- 메시지 처리 ---------------------------------------------------
    async def on_message(self, conn: NodeConn, msg: dict, rx_ts: float):
        if conn.last_seq >= 0 and msg["seq"] != conn.last_seq + 1:
            log.debug("%s seq 불연속: %d -> %d", conn.name, conn.last_seq, msg["seq"])
        conn.last_seq = msg["seq"]

        self.db.insert_message(msg, rx_ts)
        t = msg["type"]
        log.debug("<- %s %s seat=%s %s", conn.name, t, msg.get("seat"), msg["value"])

        if t == "heartbeat":
            conn.last_heartbeat = rx_ts
            if not conn.online:
                conn.online = True
                log.info("하트비트 복구: %s", conn.name)
            self.db.upsert_node_status(conn.name, msg["ts"], msg["value"])
        elif t == "node_info":
            self.on_node_info(conn, msg)
        elif t in INCIDENT_PRIORITY:
            log.info("사건: %s %s seat=%s %s", conn.name, t, msg.get("seat"), msg["value"])
            await self.send(conn.name, "ack", {"seq": msg["seq"]})

    def on_node_info(self, conn: NodeConn, msg: dict):
        value = msg["value"]
        pc = value.get("pc") or {}
        host = pc.get("hostname")
        if host:
            prev = self.last_host.get(conn.name)
            if prev and prev != host:
                log.warning("%s 의 PC 가 바뀜: %s -> %s", conn.name, prev, host)
            self.last_host[conn.name] = host
            conn.hostname = host
            self.db.upsert_node_pc(conn.name, pc, conn.peer, conn.connected_ts, msg["ts"])
        self.db.upsert_node_info(conn.name, msg["ts"], value)
        log.info("노드 정보: %s @ %s (%s, %s) 카메라=%s 좌석=%s", conn.name, host or "?",
                 pc.get("ip") or conn.peer, pc.get("os") or "?", value.get("camera"),
                 [s["seat"] for s in value.get("seats", [])])

    # ---- 백그라운드 작업 -----------------------------------------------
    async def heartbeat_watchdog(self):
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            for conn in self.nodes.values():
                if conn.online and now - conn.last_heartbeat > HEARTBEAT_TIMEOUT_S:
                    conn.online = False
                    log.warning("하트비트 끊김: %s", conn.name)

    async def run(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        log.info("hub 서버 시작: %s:%d (DB: %s)", self.host, self.port, self.db_path)
        watchdog = asyncio.create_task(self.heartbeat_watchdog())
        try:
            async with server:
                await server.serve_forever()
        finally:
            watchdog.cancel()
            self.db.close()


def main():
    ap = argparse.ArgumentParser(description="hub 서버 (TCP + DB)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="열어 둘 주소 (기본 0.0.0.0 = 이 노트북의 모든 네트워크)")
    ap.add_argument("--port", type=int, default=None,
                    help="기본: config/common.yaml 의 hub_port")
    ap.add_argument("--db", default=str(DB_PATH), help="기본: src/hub.db")
    ap.add_argument("-v", "--verbose", action="store_true", help="받은 메시지 전부 출력")
    args = ap.parse_args()
    port = args.port or int(load_config().get("hub_port", DEFAULT_PORT))

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(HubServer(args.host, port, args.db).run())
    except KeyboardInterrupt:
        log.info("종료")


if __name__ == "__main__":
    main()
