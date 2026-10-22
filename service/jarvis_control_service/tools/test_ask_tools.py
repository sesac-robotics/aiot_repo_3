"""
[질의] ask_tools 단위 테스트: 임시 DB 에 이벤트를 만들고 도구 6개의 결과를 확인한다.
확인: 검색 조건(종류·좌석·기간·상태), 통계, 타임라인, 열린 이벤트, 장비, 반 집계, 인자 오류, 학생 이름이 안 나가는지

실행 (service/jarvis_control_service 폴더에서):  python3 -m unittest tools.test_ask_tools -v
"""

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.ask_tools import AskTools                    # noqa: E402
from hub.control_config import CONTROL_DEFAULTS       # noqa: E402
from hub.db import Database                           # noqa: E402
from hub.event_manager import EventManager            # noqa: E402

TODAY_10 = datetime.combine(datetime.now().date(), datetime.min.time()).timestamp() + 10 * 3600
NAME = "홍길동테스트"


class AskToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        cfg = copy.deepcopy(CONTROL_DEFAULTS)
        self.db.sync_seats(cfg["seats"])
        self.db.register_seat(3, NAME, {"ear_shoulder": 1.0}, None, TODAY_10 - 600)   # 이름이 새면 안 된다
        self.m = EventManager(cfg)
        self.now = TODAY_10 + 3600
        self.tools = AskTools(self.db, self.m, clock=lambda: self.now)
        # 어제 쓰러짐 1건 (완료)
        y = TODAY_10 - 86400
        self.do(self.m.on_incident("fall", 1, "start", y))
        self.do(self.m.on_confirm(1, y + 8, "instructor_console"))
        self.do(self.m.on_resolve(1, y + 60, "instructor_console"))
        # 오늘: 3번 쓰러짐 (12초 뒤 확인, 40초 뒤 완료), 2번 질문 (열림), 4번 질문 (오감지)
        self.do(self.m.on_incident("fall", 3, "start", TODAY_10))
        self.do(self.m.on_confirm(2, TODAY_10 + 12, "instructor_console"))
        self.do(self.m.on_resolve(2, TODAY_10 + 40, "instructor_console"))
        self.do(self.m.on_incident("question", 2, "start", TODAY_10 + 100))
        self.do(self.m.on_incident("question", 4, "start", TODAY_10 + 200))
        self.do(self.m.on_dismiss(4, TODAY_10 + 230, "instructor_console"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def do(self, fx):
        self.db.apply(fx.saves, fx.logs)

    def run_tool(self, name, **args):
        out = self.tools.run(name, args)
        self.assertNotIn(NAME, json.dumps(out, ensure_ascii=False), f"{name} 결과에 학생 이름이 있음")
        return out

    # ---- search_events ----
    def test_search_today_falls(self):
        r = self.run_tool("search_events", kind="fall", since="today")
        self.assertEqual(r["count"], 1)
        e = r["events"][0]
        self.assertEqual((e["seat"], e["kind"], e["close_reason"], e["confirm_after_s"], e["close_after_s"]),
                         (3, "fall", "resolved", 12.0, 40.0))

    def test_search_all_and_filters(self):
        self.assertEqual(self.run_tool("search_events")["count"], 4)
        self.assertEqual(self.run_tool("search_events", seat=2)["count"], 1)
        self.assertEqual(self.run_tool("search_events", status="open")["count"], 1)
        self.assertEqual(self.run_tool("search_events", status="false_alarm")["events"][0]["seat"], 4)
        day = datetime.fromtimestamp(TODAY_10).strftime("%Y-%m-%d")
        r = self.run_tool("search_events", since=f"{day} 10:01", until=f"{day} 10:05")
        self.assertEqual([e["seat"] for e in r["events"]], [4, 2])                # 최신 순

    def test_search_limit_truncated(self):
        r = self.run_tool("search_events", limit=2)
        self.assertEqual((r["count"], r["returned"], r["truncated"]), (4, 2, True))

    def test_bad_args_return_error(self):
        for args in ({"kind": "sleep"}, {"seat": 9}, {"since": "어제쯤"}, {"status": "weird"}, {"limit": "many"}):
            self.assertIn("error", self.tools.run("search_events", args), args)
        self.assertIn("error", self.tools.run("search_events", {"student": "김"}))   # 모르는 인자
        self.assertIn("error", self.tools.run("drop_table", {}))

    def test_small_model_time_mistakes(self):                         # 10/22 qwen2.5:3b 가 실제로 넣은 값들
        self.assertEqual(self.run_tool("search_events", kind="fall", since="today", until="today")["count"], 1)
        self.assertEqual(self.run_tool("search_events", kind="fall", since="yesterday", until="yesterday")["count"], 1)
        r = self.run_tool("search_events", kind="fall", since="2099-01-01 00:00")     # 미래 since → 고치라는 오류
        self.assertIn("보다 뒤라 결과가 없다", r["error"])
        r = self.run_tool("search_events", since="today", until="yesterday")
        self.assertIn("error", r)

    def test_zero_result_note(self):
        r = self.run_tool("search_events", kind="away")
        self.assertEqual(r["count"], 0)
        self.assertIn("0건", r["note"])

    # ---- 나머지 도구 ----
    def test_open_events(self):
        r = self.run_tool("get_open_events")
        self.assertEqual(r["count"], 1)
        self.assertEqual((r["events"][0]["seat"], r["events"][0]["kind"], r["tracking_event_id"]), (2, "question", 3))

    def test_timeline(self):
        r = self.run_tool("get_event_timeline", event_id=2)
        self.assertEqual([l["action"] for l in r["timeline"]][:2], ["detect", "activate"])
        self.assertIn("confirm", [l["action"] for l in r["timeline"]])
        self.assertIn("error", self.run_tool("get_event_timeline", event_id=999))

    def test_stats_today(self):
        r = self.run_tool("get_stats", since="today")
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["by_kind"]["fall"], {"count": 1, "avg_confirm_s": 12.0, "max_confirm_s": 12.0,
                                                "avg_resolve_s": 40.0})
        self.assertEqual(r["by_kind"]["question"]["count"], 2)
        self.assertEqual(r["by_result"], {"resolved": 1, "open": 1, "false_alarm": 1})

    def test_device_status(self):
        self.db.reset_devices(self.now - 100)
        self.db.set_device_online("vision_service", True, self.now - 5)
        r = self.run_tool("get_device_status")
        self.assertTrue(any(d["online"] for d in r["devices"]))
        self.assertTrue(r["offline"])
        self.assertIn("끊김", r["summary"])
        self.assertNotIn("last_seen_s_ago", json.dumps(r))                         # 초 숫자 대신 '3분 전'
        self.assertTrue(all(isinstance(d["last_seen"], str) for d in r["devices"]))

    def test_class_condition(self):
        self.assertEqual(self.run_tool("get_class_condition")["samples"], 0)
        for i in range(6):
            self.db.insert_class_condition(self.now - 600 + i * 60, 0.25 * (i % 2), 0.5, 4)
        r = self.run_tool("get_class_condition", minutes=30)
        self.assertEqual(r["samples"], 6)
        self.assertEqual(r["latest"]["reporting"], 4)


if __name__ == "__main__":
    unittest.main()
