"""
이벤트 녹화 테스트 (Data Structure v2 event_clip): 가짜 cam2 JPEG 를 넣어 MP4 와 DB 행을 확인한다.
- ClipRecorder: 고정 fps 로 솎기·끊김 채우기, 영상이 없으면 파일을 남기지 않음
- HubServer: 사건 확정 -> event_clip(status 1, path clips/{event_id}.mp4) -> 완료 -> status 2 + keep_until,
             보관 기간 지나면 파일 삭제 + status 3, clip_request -> clip_info, 재시작 시 녹화 중 클립 닫기

실행 (service/jarvis_control_service 폴더에서):  python3 -m unittest tools.test_clip_recorder -v
"""

import asyncio
import copy
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2                                            # noqa: E402
import numpy as np                                    # noqa: E402

from hub.clip_recorder import ClipRecorder            # noqa: E402
from hub.control_config import CONTROL_DEFAULTS       # noqa: E402
from hub.db import CLIP_DELETED, CLIP_RECORDING, CLIP_SAVED   # noqa: E402

T0 = 1_000_000.0


def jpeg(i: int) -> bytes:
    img = np.full((240, 320, 3), (i * 7) % 255, np.uint8)
    cv2.putText(img, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
    return cv2.imencode(".jpg", img)[1].tobytes()


def frame_count(path: Path) -> int:
    cap = cv2.VideoCapture(str(path))
    n = 0
    while cap.read()[0]:
        n += 1
    cap.release()
    return n


class ClipRecorderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixed_fps_downsample(self):
        rec = ClipRecorder(fps=10)
        rec.start(7, 1, self.dir / "7.mp4", T0)
        for i in range(45):                                # 15fps 3초
            rec.feed(jpeg(i), T0 + i / 15)
        rec.stop(7, T0 + 3)
        [r] = rec.close()
        self.assertEqual((r.event_id, r.clip_id), (7, 1))
        self.assertTrue(29 <= r.frames <= 30, r.frames)   # 10fps × 3초
        self.assertEqual(frame_count(r.path), r.frames)

    def test_short_gap_filled_long_gap_joined(self):
        rec = ClipRecorder(fps=10)
        rec.start(1, 1, self.dir / "1.mp4", T0)
        rec.feed(jpeg(0), T0)
        rec.feed(jpeg(1), T0 + 1.0)                        # 1초 끊김 -> 마지막 장면으로 채움
        rec.feed(jpeg(2), T0 + 30.0)                       # 29초 끊김 -> 채우지 않고 이어 붙임
        rec.feed(jpeg(3), T0 + 30.1)
        [r] = rec.close()
        self.assertEqual(r.frames, 1 + 10 + 1 + 1)

    def test_no_frames_leaves_no_file(self):
        rec = ClipRecorder(fps=10)
        rec.start(3, 9, self.dir / "3.mp4", T0)
        rec.stop(3, T0 + 5)
        [r] = rec.close()
        self.assertEqual(r.frames, 0)
        self.assertFalse((self.dir / "3.mp4").exists())

    def test_feed_ignored_when_not_recording(self):
        rec = ClipRecorder(fps=10)
        rec.feed(jpeg(0), T0)
        self.assertEqual(rec.q.qsize(), 0)
        rec.close()


class HubClipTest(unittest.TestCase):
    """HubServer 의 녹화 연결 (네트워크·팬틸트 없이)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cfg = copy.deepcopy(CONTROL_DEFAULTS)
        self.cfg["clips"] = {"dir": str(self.base / "clips"), "fps": 10, "keep_days": 7}
        self.hub = self.make_hub()

    def tearDown(self):
        if self.hub.recorder:
            self.hub.recorder.close()
        self.hub.db.close()
        self.tmp.cleanup()

    def make_hub(self):
        from hub.hub import HubServer
        hub = HubServer("127.0.0.1", 0, self.base / "hub.db", control_cfg=self.cfg)
        hub.recorder = ClipRecorder(fps=10)
        hub.sent = []

        async def fake_send(node, type_, value, seat=None):
            hub.sent.append((node, type_, value))
            return True
        hub.send = fake_send
        return hub

    def run_(self, coro):
        return asyncio.run(coro)

    def feed(self, n, t0=None):
        """hub 는 녹화 시작을 Control 시계(time.time())로 찍으므로 프레임도 지금 시각 기준으로 넣는다."""
        t0 = time.time() if t0 is None else t0
        for i in range(n):
            self.hub.on_video_frame(2, jpeg(i), t0 + i / 15)
            self.hub.on_video_frame(1, jpeg(i), t0 + i / 15)       # cam1 은 녹화하지 않음

    def test_event_clip_lifecycle(self):
        h = self.hub
        self.run_(h.apply(h.events.on_incident("question", 2, "start", T0)))
        [row] = h.db.clips_for_event(1)
        self.assertEqual(row["status"], CLIP_RECORDING)
        self.assertEqual(row["camera"], 22)
        self.assertEqual(row["path"], str((self.base / "clips" / "1.mp4").resolve()))   # jarvis_DB 밖이면 절대 경로

        self.feed(30)
        self.run_(h.apply(h.events.on_resolve(1, T0 + 2, "instructor_console")))
        h.finish_clips(h.recorder.close())
        h.recorder = None
        [row] = h.db.clips_for_event(1)
        self.assertEqual(row["status"], CLIP_SAVED)
        self.assertAlmostEqual(row["keep_until"] - row["ended_at"], 7 * 86400)
        self.assertTrue(Path(row["path"]).exists())
        actions = [(l["action"], (l["detail"] or {}).get("state")) for l in h.db.event_logs(1)]
        self.assertIn(("clip", "start"), actions)
        self.assertIn(("clip", "saved"), actions)

        self.run_(h.on_clip_request(1))
        node, type_, info = h.sent[-1]
        self.assertEqual((node, type_, info["status"], info["path"]),
                         ("instructor_console", "clip_info", "saved", row["path"]))

        h.cleanup_clips(row["keep_until"] + 1)            # 보관 기간 지남
        [row] = h.db.clips_for_event(1)
        self.assertEqual(row["status"], CLIP_DELETED)
        self.assertFalse(Path(row["path"]).exists())

    def test_keep_days_setting_overrides_config(self):
        self.hub.db.set_setting("clip_keep_days", "3", T0)  # 강사 화면 [설정]은 문자열로 올 수 있음
        self.assertEqual(self.hub.clip_keep_days(), 3.0)

    def test_no_video_marks_clip_deleted(self):
        h = self.hub
        self.run_(h.apply(h.events.on_incident("away", 1, "start", T0)))
        self.run_(h.apply(h.events.on_incident("away", 1, "end", T0 + 5)))
        h.finish_clips(h.recorder.close())
        h.recorder = None
        [row] = h.db.clips_for_event(1)
        self.assertEqual(row["status"], CLIP_DELETED)
        self.run_(h.on_clip_request(99))
        self.assertEqual(h.sent[-1][2]["status"], "none")

    def test_restart_closes_recording_and_resumes_with_new_file(self):
        h = self.hub
        self.run_(h.apply(h.events.on_incident("fall", 3, "start", T0)))
        self.feed(15)
        h.recorder.close()                                # 강제 종료 흉내 (DB 는 녹화 중 그대로)
        h.recorder = None
        h.db.close()

        h2 = self.hub = self.make_hub()                  # 재시작
        [old] = h2.db.clips_for_event(1)
        self.assertEqual(old["status"], CLIP_SAVED)
        self.run_(h2.apply(h2.events.start(T0 + 10)))
        rows = h2.db.clips_for_event(1)
        self.assertEqual([r["status"] for r in rows], [CLIP_SAVED, CLIP_RECORDING])
        self.assertTrue(rows[1]["path"].endswith("1_2.mp4"))
        self.run_(h2.on_clip_request(1))
        self.assertEqual(h2.sent[-1][2]["status"], "recording")


if __name__ == "__main__":
    unittest.main()
