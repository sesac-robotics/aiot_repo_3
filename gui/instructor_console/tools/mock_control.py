"""가짜 Control 서버 — 강사 화면 단독 테스트용 (실제 Control이 없어도 화면 개발 가능)

    python gui/instructor_console/tools/mock_control.py --port 5000 [--video]

- 접속하면 스냅샷(좌석·열린 이벤트·대기열·장비·설정) 전송
- 15초쯤마다 무작위 이벤트 발생, 확인/완료/오감지 처리, 질문 3분 만료(여기선 60초)
- log_query 검색, clip_request, setting_set, seat_config, preset_save 응답
- --video: Pillow가 있으면 cam1/cam2 가짜 영상을 UDP 5011/5012로 보냄
"""
from __future__ import annotations

import argparse
import asyncio
import random
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from common import protocol as P  # noqa: E402

NAMES = {1: "김하늘", 2: "이도윤", 3: "박서연", 4: ""}           # 4번은 미등록
NEIGHBORS = {1: 0b0110, 2: 0b1001, 3: 0b1001, 4: 0b0110}         # bit(n-1)
EXPLAIN = {1: "{s}번 학생이 3초 넘게 엎드려 움직이지 않습니다. 바로 확인해 주세요.",
           2: "{s}번 학생이 손을 들고 있습니다. 대기 1건 뒤에 응답할 수 있습니다.",
           3: "{s}번 자리가 10초 넘게 비어 있습니다."}


class MockControl:
    def __init__(self, fast: bool):
        self.clients: list[asyncio.StreamWriter] = []
        self.seq = P.SeqCounter()
        self.seats = {s: {"seat": s, "neighbors": NEIGHBORS[s], "has_preset": int(s != 4),
                          "device": f"student_client_{s}", "reg_id": s * 10 if NAMES[s] else 0,
                          "student_name": NAMES[s]} for s in NEIGHBORS}
        self.events: dict[int, dict] = {}
        self.next_id = 1
        self.settings = {"alert_sound": "1", "rest_ratio": "0.5", "rest_hold_s": "300", "clip_keep_days": "7"}
        self.devices = {n: {"node": n, "kind": k, "seat": s, "online": 1, "rtt_ms": 0, "cam_ok": 1, "note": ""}
                        for n, k, s in [(0, 1, 0), (1, 1, 0), (2, 3, 0), (21, 2, 0), (22, 2, 0),
                                        (11, 4, 1), (12, 4, 2), (13, 4, 3), (14, 4, 4)]}
        self.devices[14].update(online=0, note="4번 좌석 쓰러짐 알림 못 받음")
        self.fast = fast
        self._seed_history()

    # ── 지난 이벤트 (로그 검색용) ──
    def _seed_history(self):
        now = time.time()
        for i in range(70):
            seat = random.choice([1, 2, 3])
            kind = random.choice([1, 2, 2, 3, 3, 3])
            t = now - random.uniform(600, 9 * 86400)
            self._new_event(seat, kind, t, closed=True)

    def _new_event(self, seat, kind, t, closed=False):
        e = {"event_id": self.next_id, "seat": seat, "kind": kind, "status": P.ST_CLOSED if closed else P.ST_WAITING,
             "alert_stage": 0, "close_reason": random.choice([1, 1, 2, 3]) if closed else 0, "hand_down": 0,
             "detected_at": t, "confirmed_at": t + 5 if closed else 0.0, "closed_at": t + 40 if closed else 0.0,
             "explain_mode": P.EXPLAIN_GEMINI, "explain_text": EXPLAIN[kind].format(s=seat),
             "student_name": NAMES[seat], "has_clip": 1}
        self.events[e["event_id"]] = e
        self.next_id += 1
        return e

    # ── 송신 ──
    def frame(self, t, body=None):
        return P.encode(t, P.NODE_CONTROL, self.seq.next(), body)

    def broadcast(self, t, body=None):
        data = self.frame(t, body)
        for w in list(self.clients):
            w.write(data)

    def queue(self):
        opens = sorted((e for e in self.events.values() if e["status"] != P.ST_CLOSED),
                       key=lambda e: (e["kind"], e["detected_at"]))
        for i, e in enumerate(opens):
            e["status"] = P.ST_ACTIVE if i == 0 else P.ST_WAITING
        return {"active": opens[0]["event_id"] if opens else 0, "waiting": [e["event_id"] for e in opens[1:]]}

    def push_event(self, e):
        self.broadcast(P.EVENT_UPDATE, e)

    def requeue(self):
        before = {e["event_id"]: e["status"] for e in self.events.values()}
        q = self.queue()
        for e in self.events.values():
            if before.get(e["event_id"]) != e["status"]:
                self.push_event(e)
        self.broadcast(P.QUEUE_STATE, q)

    async def snapshot(self, w):
        w.write(self.frame(P.SNAPSHOT_BEGIN))
        for s in self.seats.values():
            w.write(self.frame(P.SEAT_INFO, s))
        for e in self.events.values():
            if e["status"] != P.ST_CLOSED:
                w.write(self.frame(P.EVENT_UPDATE, e))
        w.write(self.frame(P.QUEUE_STATE, self.queue()))
        for d in self.devices.values():
            w.write(self.frame(P.DEVICE_UPDATE, d))
        for k, v in self.settings.items():
            w.write(self.frame(P.APP_SETTING, {"key": k, "value": v}))
        w.write(self.frame(P.CLASS_CONDITION, {"focus_low_ratio": 0.25, "posture_bad_ratio": 0.33, "n": 3}))
        w.write(self.frame(P.SNAPSHOT_END))
        await w.drain()

    # ── 수신 ──
    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        print("접속:", peer)
        fr = P.FrameReader()
        self.clients.append(writer)
        try:
            while data := await reader.read(65536):
                for h, name, body in fr.feed(data):
                    if name != "heartbeat":
                        print("←", name, body)
                    await self.on_msg(writer, h, name, body)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            self.clients.remove(writer)
            print("끊김:", peer)

    async def on_msg(self, w, h, name, b):
        now = time.time()
        if name == "node_info":
            await self.snapshot(w)
        elif name == "heartbeat":
            w.write(self.frame(P.HEARTBEAT_ACK, {"echo_ts": h["ts"]}))
        elif name in ("confirm", "resolve", "dismiss"):
            e = self.events.get(b["event_id"])
            if not e or e["status"] == P.ST_CLOSED:
                return
            if name == "confirm":
                e["confirmed_at"] = e["confirmed_at"] or now
            else:
                e["status"], e["closed_at"] = P.ST_CLOSED, now
                e["close_reason"] = P.CLOSE_RESOLVED if name == "resolve" else P.CLOSE_FALSE_ALARM
                e["confirmed_at"] = e["confirmed_at"] or now
            self.push_event(e)
            self.requeue()
        elif name == "log_query":
            items = [e for e in self.events.values()
                     if (not b["kind"] or e["kind"] == b["kind"]) and (not b["seat"] or e["seat"] == b["seat"])
                     and (not b["student_name"] or b["student_name"] in e["student_name"])
                     and (not b["from"] or e["detected_at"] >= b["from"]) and (not b["to"] or e["detected_at"] < b["to"])]
            items.sort(key=lambda e: -e["detected_at"])
            page = items[b["offset"]:b["offset"] + 50]
            w.write(self.frame(P.LOG_RESULT, {"req": b["req"], "total": len(items), "items": page}))
        elif name == "clip_request":
            e = self.events.get(b["event_id"])
            st = 0 if not e else (P.CLIP_RECORDING if e["status"] != P.ST_CLOSED else P.CLIP_SAVED)
            w.write(self.frame(P.CLIP_INFO, {"event_id": b["event_id"], "status": st,
                                             "started_at": e["detected_at"] if e else 0, "ended_at": e["closed_at"] if e else 0,
                                             "path": f"clips/{b['event_id']}.mp4"}))
        elif name == "setting_set":
            self.settings[b["key"]] = b["value"]
            self.broadcast(P.APP_SETTING, b)
        elif name == "seat_config":
            self.seats[b["seat"]].update(device=b["device"], neighbors=b["neighbors"])
            self.broadcast(P.SEAT_INFO, self.seats[b["seat"]])
        elif name == "preset_save":
            self.seats[b["seat"]]["has_preset"] = 1
            self.broadcast(P.SEAT_INFO, self.seats[b["seat"]])

    # ── 주기 작업 ──
    async def simulate(self):
        tick = 0
        while True:
            await asyncio.sleep(1)
            tick += 1
            now = time.time()
            if tick % (5 if self.fast else 15) == 0 and len([e for e in self.events.values() if e["status"] != 3]) < 3:
                seat = random.choice([1, 2, 3])
                kind = random.choice([1, 2, 2, 3])
                if not any(e["seat"] == seat and e["kind"] == kind and e["status"] != 3 for e in self.events.values()):
                    e = self._new_event(seat, kind, now)
                    print("이벤트 발생:", e["event_id"], seat, kind)
                    self.push_event(e)
                    self.requeue()
            for e in list(self.events.values()):
                if e["status"] == P.ST_CLOSED:
                    continue
                age = now - e["detected_at"]
                if e["kind"] == 1 and not e["confirmed_at"]:
                    stage = 2 if age >= 30 else 1 if age >= 15 else 0
                    if stage != e["alert_stage"]:
                        e["alert_stage"] = stage
                        self.push_event(e)
                if e["kind"] == 2 and age > 60:
                    e.update(status=P.ST_CLOSED, close_reason=P.CLOSE_EXPIRED, closed_at=now)
                    self.push_event(e); self.requeue()
            if tick % 2 == 0:
                d = self.devices[11]
                d["rtt_ms"] = random.randint(3, 18)
                self.broadcast(P.DEVICE_UPDATE, d)
            if tick % 10 == 0:
                self.broadcast(P.CLASS_CONDITION, {"focus_low_ratio": random.uniform(0.1, 0.7),
                                                   "posture_bad_ratio": random.uniform(0.0, 0.5), "n": 3})
            if tick == 20:
                self.broadcast(P.REST_SUGGEST, {"ratio": 0.67, "since": now - 320})
            for w in self.clients:
                try:
                    await w.drain()
                except ConnectionError:
                    pass


async def fake_video():
    """cam1/cam2 가짜 영상 (IF-01 형식: 헤더 16바이트 + JPEG)"""
    from io import BytesIO

    from PIL import Image, ImageDraw
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fid = 0
    while True:
        fid += 1
        for cam, port, color in ((1, 5011, (40, 70, 90)), (2, 5012, (90, 50, 40))):
            img = Image.new("RGB", (640, 480), color)
            d = ImageDraw.Draw(img)
            x = 40 + (fid * 7) % 520
            d.ellipse([x, 200, x + 80, 280], fill=(230, 230, 230))
            d.text((20, 20), f"cam{cam}  frame {fid}  {time.strftime('%H:%M:%S')}", fill=(255, 255, 255))
            buf = BytesIO(); img.save(buf, "JPEG", quality=70)
            sock.sendto(struct.pack(">B3xId", cam, fid, time.time()) + buf.getvalue(), ("127.0.0.1", port))
        await asyncio.sleep(0.2)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--video", action="store_true")
    ap.add_argument("--fast", action="store_true", help="이벤트를 5초마다 발생")
    a = ap.parse_args()
    mc = MockControl(a.fast)
    server = await asyncio.start_server(mc.handle, "0.0.0.0", a.port)
    print(f"가짜 Control :{a.port} 대기 중")
    tasks = [mc.simulate()] + ([fake_video()] if a.video else [])
    async with server:
        await asyncio.gather(server.serve_forever(), *tasks)


if __name__ == "__main__":
    asyncio.run(main())
