"""
control(hub) TCP 서버 (상세 설계 0·5·6장)

- 노드 <-> control: TCP 위의 바이너리 프레임 v3 (common/protocol.py: encode / FrameReader / parse_message), 포트 5000
- 원본 메시지는 DB 에 기록하지 않는다 (Data Structure v2: messages·nodes·node_status 폐지)
- heartbeat -> device.last_seen (학생: rtt_ms·cam_ok, vision: cam1/cam2 -> 장비 21·22)
- device_update -> device + device_log, class_condition -> class_condition 표
- 사건(fall / question / away)에는 rx_ack 로 수신 확인을 보내고 event_manager 에 넘긴다
- 판단은 하지 않는다. 입력을 event_manager 에 넘기고, 돌려받은 할 일을
  db.py 로 기록하고 해당 노드에 보낸다.
- 0.1초마다 event_manager.tick(now) (control 시계)
- 하트비트가 6초(3회) 안 오면 offline -> device_update
- [팬틸트] 팬틸트는 TCP 노드가 아니라 같은 프로세스의 PanTiltService(hub/pantilt).
  to="pantilt" 할 일은 메서드 호출로 실행하고, 팬틸트 알림(on_event)은 이벤트 루프로 넘겨 처리한다.
- 학생 노드 수신 기록 (로거 "hub.student"): condition_summary·fall_confirm 을 받을 때마다 "수신 OK",
  형식이 틀려 버린 메시지는 "수신 실패", fall_alert 를 보내면 "전송 OK".
  접속 중인데 condition_summary 가 30초 없으면 경고하고, 1분마다 좌석별 수신 현황을 한 줄로 남긴다.
- 카메라 영상: camera_streamer 의 UDP(6001/6002)를 저장하지 않고 instructor_console(5011/5012)로 넘긴다
  (hub/video_relay.py). --no-video 면 끈다.
- 로그는 화면과 logs/hub.log (5MB × 5개 순환) 에 같이 남는다. 학생 수신만 보려면: grep hub.student logs/hub.log

실행 (service/jarvis_control_service 폴더에서):  python3 -m hub [--host 0.0.0.0] [--port 5000] [--db 경로] [--log-file 경로] [-v]
                                                 [--pantilt-port auto|/dev/ttyACM0] [--pantilt-sim] [--no-video]
"""

import argparse
import asyncio
import logging
import logging.handlers
import time
from dataclasses import dataclass

from common.config import load_config
from common.protocol import (VISION_NODE, CONSOLE_NODE, CONTROL_NODE, DEFAULT_PORT, HEARTBEAT_TIMEOUT_S,
                             INCIDENT_TYPES, NOT_LOGGED_TYPES, FrameReader, MessageError, encode,
                             STUDENT_NODES, make_message, parse_message, student_seat)
from hub.control_config import DB_PATH, SERVICE_DIR, ConfigError, load_control_config
from hub.db import Database, SchemaMismatch
from hub.event_manager import PANTILT, Effects, EventManager, HelpEvent   # [팬틸트] PANTILT 추가
from hub.pantilt import PanTiltService                                    # [팬틸트]
from hub.video_relay import VideoRelay

log = logging.getLogger("hub")
slog = logging.getLogger("hub.student")      # 학생 노드 수신·송신 기록 (grep hub.student)

LOG_PATH = SERVICE_DIR / "logs" / "hub.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
STUDENT_SUMMARY_TIMEOUT_S = 30.0             # condition_summary 가 이만큼 없으면 경고 (10초 주기 × 3, 반 집계 유효 시간과 같음)
STUDENT_REPORT_INTERVAL_S = 60.0             # 좌석별 수신 현황 한 줄 요약 주기
STUDENT_SEND_TYPES = {"fall_alert", "fall_alert_cancel"}

TICK_INTERVAL_S = 0.1
QUIET_SEND_TYPES = {"track_target", "explain_request"}   # 미접속이어도 경고 안 함 ([팬틸트] pan_tilt 는 TCP 로 안 보냄)


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
    summary_count: int = 0                 # 이 연결에서 받은 condition_summary 수 (학생)
    last_summary_ts: float | None = None
    last_summary: dict | None = None
    summary_stale: bool = False            # 30초 없음 경고를 이미 냈는지


class HubServer:
    def __init__(self, host: str, port: int, db_path, control_cfg: dict | None = None,
                 pantilt_port: str | None = None, pantilt_sim: bool = False,   # [팬틸트] 인자 2개 추가
                 video: bool = True):
        self.host, self.port = host, port
        self.db_path = db_path
        self.db = Database(db_path)
        self.db.reset_devices(time.time())
        cfg = control_cfg or load_control_config()
        self.db.sync_seats(cfg["seats"])
        open_events = [HelpEvent(**row) for row in self.db.load_open_events()]
        if open_events:
            log.info("재시작 전 열린 이벤트 %d건 복구", len(open_events))
        self.events = EventManager(cfg, self.db.max_event_id() + 1, open_events)
        self.nodes: dict[str, NodeConn] = {}
        self.seq = 0
        # [팬틸트] 팬틸트 모듈은 run() 에서 이벤트 루프가 생긴 뒤 만든다 (on_event 를 루프로 넘기기 위해)
        self.pantilt: PanTiltService | None = None                                        # [팬틸트]
        self.pantilt_port = pantilt_port or cfg.get("pantilt", {}).get("port", "auto")   # [팬틸트] control.yaml pantilt.port
        self.pantilt_sim = pantilt_sim                                                    # [팬틸트]
        self.video = VideoRelay(host, self.console_ip, vision_ip=self.vision_ip) if video else None   # 영상: streamer -> Console 중계
        self.loop: asyncio.AbstractEventLoop | None = None                               # [팬틸트]
        self._last_report = time.time()
        self._report_counts: dict[str, int] = {}    # 지난 현황 요약 때의 summary_count

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
            data = encode(msg)
        except MessageError as e:          # event_manager 가 보낼 수 없는 값을 만든 경우 (버그) - 서버는 계속
            log.error("%s 에 보낼 %s 를 인코딩하지 못함: %s", node, type_, e)
            return False
        try:
            conn.writer.write(data)
            await conn.writer.drain()
            log.debug("-> %s %s %s", node, type_, value)
            if node in STUDENT_NODES and type_ in STUDENT_SEND_TYPES:
                slog.info("전송 OK -> %s %s %s", node, type_, value)
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
            self.record_sends(fx.sends, time.time())
        except Exception:                  # 기록이 실패해도 화면·장비에는 보낸다 (쓰러짐을 놓치지 않게)
            log.exception("DB 기록 실패 - 전송은 계속")
        for s in fx.sends:
            if s.to == PANTILT:                                  # [팬틸트] TCP 대신 메서드 호출
                self.call_pantilt(s.type, s.value)               # [팬틸트]
            elif s.to == "db":                                   # [팬틸트] 팬틸트 프리셋 저장 → seat.has_preset
                self.record_db(s.type, s.value)                  # [팬틸트]
            else:
                await self.send(s.to, s.type, s.value, s.seat)

    def record_sends(self, sends, now: float):
        """보내는 메시지 중 DB 에 남기는 것 (판단 없이 내용 그대로 기록)."""
        for s in sends:
            if s.type == "device_update":
                try:
                    self.db.set_device_online(s.value["node"], s.value["online"], now,
                                              s.value.get("note"))
                except ValueError:         # device 표에 없는 노드 (템플릿 노드·pantilt 등)
                    log.debug("device 표에 없는 노드: %s", s.value.get("node"))
            elif s.type == "class_condition":
                self.db.insert_class_condition(now, s.value["focus_low_ratio"],
                                               s.value["posture_bad_ratio"],
                                               s.value.get("reporting", 0))

    # ---- [팬틸트] 팬틸트 모듈 연결 --------------------------------------
    def call_pantilt(self, type_: str, v: dict):                                  # [팬틸트] 추가
        """event_manager 의 to="pantilt" 할 일을 PanTiltService 메서드로 바꿔 부른다 (바로 반환)."""
        p = self.pantilt
        if p is None:
            log.debug("팬틸트 시작 전 - %s 무시", type_)
            return
        if type_ == "preset":
            p.preset(v["gen"], v["seat"], v["kind"])
        elif type_ == "home":
            p.home(v["gen"])
        elif type_ == "track":
            p.track(v["gen"], v["cx"], v["cy"], v["captured_at"], v["frame_id"], v["found"])
        elif type_ == "jog":
            p.jog(v["dpan"], v["dtilt"])
        elif type_ == "save":
            p.save_preset(v["seat"])
        elif type_ == "vision_online":
            p.set_vision_online(v["online"])
        else:
            log.warning("모르는 팬틸트 명령: %s", type_)

    def record_db(self, type_: str, v: dict):                                     # [팬틸트] 추가
        if type_ == "seat_preset":
            try:
                self.db.set_has_preset(v["seat"], v["has_preset"])   # db.py 에 없으면 추가 필요 (아래 설명)
            except Exception:
                log.exception("seat.has_preset 기록 실패: %s", v)

    def on_pantilt_event(self, event: str, fields: dict):                         # [팬틸트] 추가
        """PanTiltService 의 on_event. 팬틸트 스레드에서 불리므로 이벤트 루프로 넘긴다."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self.handle_pantilt_event(event, fields)))

    async def handle_pantilt_event(self, event: str, fields: dict):               # [팬틸트] 추가
        now = time.time()
        try:
            if event == "pan_tilt_state":
                await self.apply(self.events.on_pantilt_state(fields, now))
            elif event == "preset_saved":
                await self.apply(self.events.on_preset_saved(fields, now))
        except Exception:
            log.exception("팬틸트 이벤트 처리 오류: %s %s", event, fields)

    # ---- 연결 처리 -----------------------------------------------------
    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        log.info("접속: %s", peer)
        node_name = None
        frames = FrameReader()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                rx_ts = time.time()
                received = frames.feed(chunk)
                while frames.errors:       # 시작 표시 없음·CRC 불일치: 바이트를 건너뛰고 연결은 유지
                    (slog if node_name in STUDENT_NODES else log).warning(
                        "%s(%s) 스트림 오류 - 바이트 버림: %s", node_name or "?", peer, frames.errors.pop(0))
                for frame in received:
                    node_name = await self.on_frame(frame, node_name, writer, peer, rx_ts)
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

    async def on_frame(self, frame: bytes, node_name, writer, peer, rx_ts: float):
        """프레임 하나 처리. 연결의 노드 이름(처음이면 등록한 이름)을 돌려준다."""
        try:
            msg = parse_message(frame)
        except MessageError as e:
            (slog if node_name in STUDENT_NODES else log).warning(
                "수신 실패 %s(%s) - 잘못된 메시지 버림: %s", node_name or "?", peer, e)
            return node_name

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
        elif msg["node"] != node_name:
            log.warning("%s 연결에서 다른 노드 이름(%s)의 메시지 - 버림", node_name, msg["node"])
            return node_name

        try:
            await self.on_message(self.nodes[node_name], msg, rx_ts)
        except Exception:                  # 메시지 하나의 오류로 연결을 끊지 않는다
            log.exception("%s 메시지 처리 중 오류 - 이 메시지만 버림: %s", node_name, msg)
        return node_name

    def vision_ip(self):
        conn = self.nodes.get(VISION_NODE)
        return conn.peer.rsplit(":", 1)[0] if conn and conn.online else None

    def console_ip(self) -> str | None:
        """영상을 보낼 instructor_console 의 IP (TCP 접속 주소). 접속 중이 아니면 None."""
        conn = self.nodes.get(CONSOLE_NODE)
        if not conn or not conn.online or ":" not in conn.peer:
            return None
        return conn.peer.rsplit(":", 1)[0]

    async def on_connect(self, node: str, now: float):
        if node in STUDENT_NODES:
            self._report_counts[node] = 0
        await self.apply(self.events.on_node_online(node, True, now))
        if node == CONSOLE_NODE:
            await self.send(CONSOLE_NODE, "snapshot", self.events.snapshot())

    async def set_offline(self, conn: NodeConn, reason: str):
        conn.online = False
        log.warning("%s: %s", reason, conn.name)
        await self.apply(self.events.on_node_online(conn.name, False, time.time()))

    # ---- 메시지 처리 ---------------------------------------------------
    async def on_message(self, conn: NodeConn, msg: dict, rx_ts: float):
        if conn.last_seq >= 0 and msg["seq"] != conn.last_seq + 1:
            log.debug("%s seq 불연속: %d -> %d", conn.name, conn.last_seq, msg["seq"])
        conn.last_seq = msg["seq"]

        t, v, seat = msg["type"], msg["value"], msg.get("seat")
        if t not in NOT_LOGGED_TYPES:
            log.debug("<- %s %s seat=%s %s", conn.name, t, seat, v)

        if t == "heartbeat":
            conn.last_heartbeat = rx_ts
            if not conn.online:
                conn.online = True
                log.info("하트비트 복구: %s", conn.name)
                await self.on_connect(conn.name, rx_ts)
            try:
                self.db.touch_device(conn.name, rx_ts, v)
            except ValueError:
                pass
        elif t == "node_info":
            self.on_node_info(conn, msg)
        elif t in INCIDENT_TYPES:
            log.info("사건: %s %s seat=%s %s", conn.name, t, seat, v)
            await self.send(conn.name, "rx_ack", {"seq": msg["seq"]})
            await self.apply(self.events.on_incident(
                t, seat, v["state"], rx_ts, source=f"{conn.name}:{msg['seq']}"))
        elif t == "seat_obs":
            if conn.name == VISION_NODE:
                await self.apply(self.events.on_seat_obs(v, rx_ts))
        elif t == "track_obs":                                          # [팬틸트] track_offset 대체
            await self.apply(self.events.on_track_obs(v, rx_ts))       # [팬틸트] {gen, frame_id, captured_at, status, cx, cy}
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
            if conn.name in STUDENT_NODES:
                slog.info("수신 OK %s fall_confirm target_seat=%s seq=%d", conn.name, v.get("target_seat"), msg["seq"])
            if "target_seat" in v:
                await self.apply(self.events.on_fall_confirm(v["target_seat"], rx_ts, conn.name))
        elif t == "condition_summary":
            s = student_seat(conn.name) or seat
            if seat is not None and student_seat(conn.name) not in (None, seat):
                slog.warning("%s 가 seat=%s 로 condition_summary 를 보냄 - 노드 이름의 좌석 %s 로 집계",
                             conn.name, seat, s)
            if s is None:
                slog.warning("수신 실패 %s condition_summary - 좌석을 알 수 없어 버림", conn.name)
                return
            conn.summary_count += 1
            conn.last_summary_ts, conn.last_summary = rx_ts, v
            if conn.summary_stale:
                conn.summary_stale = False
                slog.info("%s condition_summary 다시 들어옴", conn.name)
            slog.info("수신 OK %s condition_summary seat=%d focus_low=%s posture_bad=%s seq=%d (이번 연결 %d건째)",
                      conn.name, s, v["focus_low"], v["posture_bad"], msg["seq"], conn.summary_count)
            await self.apply(self.events.on_condition_summary(s, v, rx_ts))
        elif t == "pan_tilt_jog":                                       # [팬틸트] Console 프리셋 등록 (화살표)
            await self.apply(self.events.on_pan_tilt_jog(int(v["dpan"]), int(v["dtilt"]), rx_ts))
        elif t == "preset_save":                                        # [팬틸트] Console 프리셋 등록 (등록 버튼)
            await self.apply(self.events.on_preset_save(int(v["seat"]), rx_ts))
        # [팬틸트] pan_tilt_state 분기 삭제 — 팬틸트 상태는 TCP 가 아니라 on_pantilt_event 로 온다
        else:
            log.debug("처리하지 않는 type: %s (%s)", t, conn.name)

    def on_node_info(self, conn: NodeConn, msg: dict):
        pc = msg["value"].get("pc") or {}
        host = pc.get("hostname")
        if host:
            conn.hostname = host
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
            self.check_students(now)

    # ---- 학생 수신 기록 (로그만, 판단 없음) ------------------------------
    def check_students(self, now: float):
        for node in STUDENT_NODES:
            conn = self.nodes.get(node)
            if not conn or not conn.online or conn.summary_stale:
                continue
            since = conn.last_summary_ts or conn.connected_ts
            if now - since > STUDENT_SUMMARY_TIMEOUT_S:
                conn.summary_stale = True
                what = "마지막 수신 후" if conn.last_summary_ts else "접속 후 한 번도 없이"
                slog.warning("%s 연결은 살아 있지만 condition_summary 가 %s %.0f초째 없음 "
                             "(학생 프로그램의 판정·전송 확인)", node, what, now - since)
        if now - self._last_report >= STUDENT_REPORT_INTERVAL_S:
            self._last_report = now
            self.log_student_report(now)

    def log_student_report(self, now: float):
        if not any(n in self.nodes for n in STUDENT_NODES):
            return                         # 학생 노드가 한 번도 안 붙었으면 조용히
        parts = []
        for node in STUDENT_NODES:
            seat, conn = student_seat(node), self.nodes.get(node)
            if not conn:
                parts.append(f"{seat}번 미접속")
                continue
            recent = conn.summary_count - self._report_counts.get(node, 0)
            self._report_counts[node] = conn.summary_count
            if not conn.online:
                parts.append(f"{seat}번 끊김")
            elif conn.last_summary_ts is None:
                parts.append(f"{seat}번 접속, 데이터 없음")
            else:
                v = conn.last_summary or {}
                state = "끊김의심" if conn.summary_stale else "OK"
                parts.append(f"{seat}번 {state} 최근 {recent}건, 마지막 {now - conn.last_summary_ts:.0f}초 전 "
                             f"(focus_low={v.get('focus_low')}, posture_bad={v.get('posture_bad')})")
        slog.info("학생 수신 현황 | %s", " | ".join(parts))

    async def tick_loop(self):
        """0.1초마다 event_manager.tick (control 시계)."""
        while True:
            await asyncio.sleep(TICK_INTERVAL_S)
            try:
                await self.apply(self.events.tick(time.time()))
            except Exception:
                log.exception("tick 처리 중 오류")

    async def run(self):
        self.loop = asyncio.get_running_loop()                                    # [팬틸트]
        self.pantilt = PanTiltService(port=self.pantilt_port, simulate=self.pantilt_sim,
                                      on_event=self.on_pantilt_event)             # [팬틸트] Uno 없어도 Control 은 계속
        await self.apply(self.events.start(time.time()))                          # [팬틸트] 복구된 사건 좌석으로
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        log.info("control 서버 시작: %s:%d (DB: %s)", self.host, self.port, self.db_path)
        if self.video:
            await self.video.start()
        tasks = [asyncio.create_task(self.heartbeat_watchdog()),
                 asyncio.create_task(self.tick_loop())]
        try:
            async with server:
                await server.serve_forever()
        finally:
            for t in tasks:
                t.cancel()
            self.pantilt.close()                                                  # [팬틸트] 정지 + LED 끔
            if self.video:
                self.video.close()
            self.db.close()


def setup_logging(level: int, log_file: str | None):
    """화면 + 파일(5MB 넘으면 hub.log.1 ~ .5 로 밀어냄). 파일을 못 열면 화면에만 남긴다."""
    logging.basicConfig(level=level, format=LOG_FORMAT)
    if not log_file:
        return
    try:
        from pathlib import Path
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5,
                                                  encoding="utf-8")
    except OSError as e:
        log.warning("로그 파일을 열 수 없음 (%s) - 화면에만 남김: %s", log_file, e)
        return
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(fh)
    log.info("로그 파일: %s", log_file)


def main():
    ap = argparse.ArgumentParser(description="control 서버 (TCP + 이벤트 관리 + DB)")
    ap.add_argument("--host", default="0.0.0.0",
                    help="열어 둘 주소 (기본 0.0.0.0 = 이 노트북의 모든 네트워크)")
    ap.add_argument("--port", type=int, default=None,
                    help="기본: config/common.yaml 의 control_port")
    ap.add_argument("--db", default=str(DB_PATH), help="기본: service/jarvis_control_service/db/hub.db")
    ap.add_argument("--log-file", default=str(LOG_PATH),
                    help="로그 파일 (기본: service/jarvis_control_service/logs/hub.log, 빈 문자열이면 화면에만)")
    ap.add_argument("-v", "--verbose", action="store_true", help="주고받은 메시지 전부 출력")
    ap.add_argument("--pantilt-port", default=None,                                    # [팬틸트]
                    help="Uno 시리얼 포트 (기본: control.yaml pantilt.port, 없으면 auto)")
    ap.add_argument("--pantilt-sim", action="store_true", help="Uno 없이 모의 팬틸트")  # [팬틸트]
    ap.add_argument("--no-video", action="store_true", help="카메라 영상 중계(UDP 6001/6002) 끄기")
    args = ap.parse_args()
    port = args.port or int(load_config().get("control_port", DEFAULT_PORT))

    setup_logging(logging.DEBUG if args.verbose else logging.INFO, args.log_file)
    try:
        server = HubServer(args.host, port, args.db,
                           pantilt_port=args.pantilt_port, pantilt_sim=args.pantilt_sim,   # [팬틸트]
                           video=not args.no_video)
    except (SchemaMismatch, ConfigError) as e:
        log.error("%s", e)
        raise SystemExit(1)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        log.info("종료")


if __name__ == "__main__":
    main()