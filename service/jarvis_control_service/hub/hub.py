"""
control(hub) TCP 서버 (상세 설계 0·5·6장)

- 노드 <-> control: TCP 위의 줄 단위 JSON (common/protocol.py), 포트 5000
- 받은 메시지는 messages 표에 기록 (heartbeat, track_offset 은 제외)
- heartbeat -> node_status, node_info -> nodes(접속 PC)
- 사건(fall / question / away)에는 rx_ack 로 수신 확인을 보내고 event_manager 에 넘긴다
- 판단은 하지 않는다. 입력을 event_manager 에 넘기고, 돌려받은 할 일을
  db.py 로 기록하고 해당 노드에 보낸다.
- 0.1초마다 event_manager.tick(now) (control 시계)
- 하트비트가 6초(3회) 안 오면 offline -> device_update

실행 (service/jarvis_control_service 폴더에서):  python3 -m hub [--host 0.0.0.0] [--port 5000] [--db 경로] [-v]
"""

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass

from common.config import load_config
from common.protocol import (CONSOLE_NODE, CONTROL_NODE, DEFAULT_PORT, HEARTBEAT_TIMEOUT_S,
                             INCIDENT_TYPES, NOT_LOGGED_TYPES, MessageError, encode,
                             make_message, parse_message, student_seat)
from hub.control_config import DB_PATH, ConfigError, load_control_config
from hub.db import Database, SchemaMismatch
from hub.event_manager import Effects, EventManager, HelpEvent

log = logging.getLogger("hub")

TICK_INTERVAL_S = 0.1
QUIET_SEND_TYPES = {"pan_tilt", "track_target", "explain_request"}   # 미접속이어도 경고 안 함


@dataclass
class NodeConn:
    name: str
    writer: asyncio.StreamWriter
    last_heartbeat: float
    peer: str = ""                 # control 이 본 상대 주소 "ip:port"
    connected_ts: float = 0.0
    hostname: str | None = None    # node_info 의 pc.hostname
    last_seq: int = -1
    online: bool = True


class HubServer:
    def __init__(self, host: str, port: int, db_path, control_cfg: dict | None = None):
        self.host, self.port = host, port
        self.db_path = db_path
        self.db = Database(db_path)
        cfg = control_cfg or load_control_config()
        self.db.sync_seats(cfg["seats"])
        open_events = [HelpEvent(**row) for row in self.db.load_open_events()]
        if open_events:
            log.info("재시작 전 열린 이벤트 %d건 복구", len(open_events))
        self.events = EventManager(cfg, self.db.max_event_id() + 1, open_events)
        self.nodes: dict[str, NodeConn] = {}
        self.seq = 0

    # ---- 송신 ----------------------------------------------------------
    async def send(self, node: str, type_: str, value: dict, seat=None) -> bool:
        conn = self.nodes.get(node)
        if not conn or not conn.online:
            (log.debug if type_ in QUIET_SEND_TYPES else log.warning)(
                "%s 미접속 - %s 전송 실패", node, type_)
            return False
        self.seq += 1
        msg = make_message(CONTROL_NODE, self.seq, type_, seat, value)
        try:
            conn.writer.write(encode(msg))
            await conn.writer.drain()
            log.debug("-> %s %s %s", node, type_, value)
            return True
        except (ConnectionError, OSError) as e:
            log.warning("%s 전송 오류: %s", node, e)
            return False

    async def apply(self, fx: Effects):
        """event_manager 가 정한 할 일: DB 기록 -> 전송."""
        if not fx:
            return
        try:
            self.db.apply(fx.saves, fx.logs)
        except Exception:                  # 기록이 실패해도 화면·장비에는 보낸다 (쓰러짐을 놓치지 않게)
            log.exception("DB 기록 실패 - 전송은 계속")
        for s in fx.sends:
            await self.send(s.to, s.type, s.value, s.seat)

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
                        old.writer.close()
                    self.nodes[node_name] = NodeConn(node_name, writer, rx_ts,
                                                     peer=peer_str, connected_ts=rx_ts)
                    log.info("노드 등록: %s (%s)", node_name, peer_str)
                    await self.on_connect(node_name, rx_ts)

                try:
                    await self.on_message(self.nodes[node_name], msg, rx_ts)
                except Exception:          # 메시지 하나의 오류로 연결을 끊지 않는다
                    log.exception("%s 메시지 처리 중 오류 - 이 메시지만 버림: %s", node_name, msg)
        except (ConnectionError, asyncio.IncompleteReadError) as e:
            log.info("%s 연결 오류: %s", peer, e)
        except Exception:
            log.exception("%s(%s) 연결 처리 중 오류 - 연결을 끊음", node_name, peer)
        finally:
            conn = self.nodes.get(node_name) if node_name else None
            if conn and conn.writer is writer and conn.online:
                await self.set_offline(conn, "연결 종료")
            writer.close()
            log.info("연결 종료: %s (%s)", node_name, peer)

    async def on_connect(self, node: str, now: float):
        self.db.set_node_online(node, True)
        await self.apply(self.events.on_node_online(node, True, now))
        if node == CONSOLE_NODE:
            await self.send(CONSOLE_NODE, "snapshot", self.events.snapshot())

    async def set_offline(self, conn: NodeConn, reason: str):
        conn.online = False
        log.warning("%s: %s", reason, conn.name)
        self.db.set_node_online(conn.name, False)
        await self.apply(self.events.on_node_online(conn.name, False, time.time()))

    # ---- 메시지 처리 ---------------------------------------------------
    async def on_message(self, conn: NodeConn, msg: dict, rx_ts: float):
        if conn.last_seq >= 0 and msg["seq"] != conn.last_seq + 1:
            log.debug("%s seq 불연속: %d -> %d", conn.name, conn.last_seq, msg["seq"])
        conn.last_seq = msg["seq"]

        t, v, seat = msg["type"], msg["value"], msg.get("seat")
        if t not in NOT_LOGGED_TYPES:
            self.db.insert_message(msg, rx_ts)
            log.debug("<- %s %s seat=%s %s", conn.name, t, seat, v)

        if t == "heartbeat":
            conn.last_heartbeat = rx_ts
            if not conn.online:
                conn.online = True
                log.info("하트비트 복구: %s", conn.name)
                await self.on_connect(conn.name, rx_ts)
            self.db.upsert_node_status(conn.name, msg["ts"], v)
        elif t == "node_info":
            self.on_node_info(conn, msg)
        elif t in INCIDENT_TYPES:
            log.info("사건: %s %s seat=%s %s", conn.name, t, seat, v)
            await self.send(conn.name, "rx_ack", {"seq": msg["seq"]})
            await self.apply(self.events.on_incident(
                t, seat, v["state"], rx_ts, source=f"{conn.name}:{msg['seq']}"))
        elif t == "track_offset":
            await self.apply(self.events.on_track_offset(seat, v, rx_ts))
        elif t == "explain_text":
            if "event_id" in v:
                await self.apply(self.events.on_explain_text(
                    v["event_id"], v.get("text", ""), v.get("mode"), rx_ts))
        elif t == "confirm":
            if "event_id" in v:
                await self.apply(self.events.on_confirm(v["event_id"], rx_ts, conn.name))
        elif t == "resolve":
            if "event_id" in v:
                await self.apply(self.events.on_resolve(v["event_id"], rx_ts, conn.name))
        elif t == "fall_confirm":
            if "target_seat" in v:
                await self.apply(self.events.on_fall_confirm(v["target_seat"], rx_ts, conn.name))
        elif t == "condition_summary":
            s = student_seat(conn.name) or seat
            if s is not None:
                await self.apply(self.events.on_condition_summary(s, v, rx_ts))
        elif t == "pan_tilt_state":
            log.debug("팬틸트: %s", v)
        else:
            log.debug("처리하지 않는 type: %s (%s)", t, conn.name)

    def on_node_info(self, conn: NodeConn, msg: dict):
        pc = msg["value"].get("pc") or {}
        host = pc.get("hostname")
        if host:
            conn.hostname = host
            self.db.upsert_node_pc(conn.name, pc, conn.peer, conn.connected_ts, msg["ts"])
        log.info("노드 정보: %s @ %s (%s, %s)", conn.name, host or "?",
                 pc.get("ip") or conn.peer, pc.get("os") or "?")

    # ---- 백그라운드 작업 -----------------------------------------------
    async def heartbeat_watchdog(self):
        while True:
            await asyncio.sleep(1.0)
            now = time.time()
            for conn in list(self.nodes.values()):
                if conn.online and now - conn.last_heartbeat > HEARTBEAT_TIMEOUT_S:
                    await self.set_offline(conn, "하트비트 끊김")

    async def tick_loop(self):
        """0.1초마다 event_manager.tick (control 시계)."""
        while True:
            await asyncio.sleep(TICK_INTERVAL_S)
            try:
                await self.apply(self.events.tick(time.time()))
            except Exception:
                log.exception("tick 처리 중 오류")

    async def run(self):
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        log.info("control 서버 시작: %s:%d (DB: %s)", self.host, self.port, self.db_path)
        tasks = [asyncio.create_task(self.heartbeat_watchdog()),
                 asyncio.create_task(self.tick_loop())]
        try:
            async with server:
                await server.serve_forever()
        finally:
            for t in tasks:
                t.cancel()
            self.db.close()


def main():
    ap = argparse.ArgumentParser(description="control 서버 (TCP + 이벤트 관리 + DB)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="열어 둘 주소 (기본 0.0.0.0 = 이 노트북의 모든 네트워크)")
    ap.add_argument("--port", type=int, default=None,
                    help="기본: config/common.yaml 의 control_port")
    ap.add_argument("--db", default=str(DB_PATH), help="기본: service/jarvis_control_service/db/hub.db")
    ap.add_argument("-v", "--verbose", action="store_true", help="주고받은 메시지 전부 출력")
    args = ap.parse_args()
    port = args.port or int(load_config().get("control_port", DEFAULT_PORT))

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        server = HubServer(args.host, port, args.db)
    except (SchemaMismatch, ConfigError) as e:
        log.error("%s", e)
        raise SystemExit(1)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        log.info("종료")


if __name__ == "__main__":
    main()
