"""
event_manager 단위 테스트 (상세 설계 9장 "단위"): 가짜 시계로 사건을 순서대로 넣는다.
확인: 선점, FIFO, 15초/30초 알림, 만료, 중복 무시, confirm/resolve, 추적 nudge

실행 (service/jarvis_control_service 폴더에서):  python3 -m unittest tools.test_event_manager -v
"""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hub.control_config import CONTROL_DEFAULTS       # noqa: E402
from hub.event_manager import EventManager            # noqa: E402

T0 = 1000.0


def sends(fx, type_=None, to=None):
    return [s for s in fx.sends if (type_ is None or s.type == type_) and (to is None or s.to == to)]


class EventManagerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(CONTROL_DEFAULTS)
        self.m = EventManager(self.cfg)

    def start(self, kind, seat, t):
        return self.m.on_incident(kind, seat, "start", T0 + t, source=f"vision_service:{seat}")

    def queue(self):
        return self.m.snapshot()["queue"]

    # ---- 생성 / 중복 ----------------------------------------------------
    def test_first_event_becomes_active_and_tracked(self):
        fx = self.start("question", 2, 0)
        self.assertEqual(self.queue(), {"active": 1, "waiting": []})
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 2})
        self.assertEqual(sends(fx, "pan_tilt")[0].value, {"mode": "preset", "seat": 2, "pan": 80, "tilt": 85})
        self.assertEqual(len(sends(fx, "explain_request")), 1)
        self.assertEqual(len(sends(fx, "event_update")), 1)
        self.assertEqual([l[2] for l in fx.logs], ["detect", "activate"])
        self.assertEqual(fx.saves[-1]["status"], "active")

    def test_duplicate_start_ignored(self):
        self.start("question", 2, 0)
        fx = self.start("question", 2, 1)
        self.assertFalse(fx)
        self.assertEqual(len(self.m.events), 1)

    # ---- 우선순위 / 선점 / FIFO -----------------------------------------
    def test_fifo_same_priority(self):
        self.start("question", 2, 0)
        self.start("question", 4, 1)
        self.start("question", 1, 2)
        self.assertEqual(self.queue(), {"active": 1, "waiting": [2, 3]})

    def test_fall_preempts_question(self):
        self.start("question", 2, 0)
        fx = self.start("fall", 3, 1)
        self.assertEqual(self.queue(), {"active": 2, "waiting": [1]})
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 3})
        self.assertIn("preempt", [l[2] for l in fx.logs])

    def test_away_waits_behind_question(self):
        self.start("away", 1, 0)
        self.start("question", 2, 1)
        self.assertEqual(self.queue(), {"active": 2, "waiting": [1]})

    def test_resolve_moves_to_next(self):
        self.start("question", 2, 0)
        self.start("fall", 3, 1)
        fx = self.m.on_resolve(2, T0 + 5, "instructor_console")
        self.assertEqual(self.queue(), {"active": 1, "waiting": []})
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 2})
        fx = self.m.on_resolve(1, T0 + 6, "instructor_console")
        self.assertEqual(self.queue(), {"active": None, "waiting": []})
        self.assertEqual(sends(fx, "pan_tilt")[0].value, {"mode": "home"})
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": None})

    # ---- end 처리 -------------------------------------------------------
    def test_question_end_only_marks_hand_down(self):
        self.start("question", 2, 0)
        fx = self.m.on_incident("question", 2, "end", T0 + 3)
        self.assertTrue(self.m.events[1].hand_down)
        self.assertEqual(self.m.events[1].status, "active")
        self.assertTrue(sends(fx, "event_update")[0].value["hand_down"])

    def test_fall_end_does_not_close(self):
        self.start("fall", 3, 0)
        self.m.on_incident("fall", 3, "end", T0 + 3)
        self.assertTrue(self.m.events[1].risen)
        self.assertEqual(self.m.events[1].status, "active")

    def test_away_end_closes_cleared(self):
        self.start("away", 1, 0)
        fx = self.m.on_incident("away", 1, "end", T0 + 20)
        self.assertEqual(self.m.events, {})
        closed = [r for r in fx.saves if r["status"] == "closed"][0]
        self.assertEqual(closed["close_reason"], "cleared")
        self.assertEqual(closed["closed_at"], T0 + 20)

    # ---- 쓰러짐 단계 알림 ----------------------------------------------
    def test_fall_alert_stages(self):
        self.start("fall", 2, 0)
        self.assertFalse(sends(self.m.tick(T0 + 14.9), "fall_alert"))
        fx = self.m.tick(T0 + 15.0)
        alerts = sends(fx, "fall_alert")
        self.assertEqual(sorted(a.to for a in alerts), ["student_client_1", "student_client_3"])
        self.assertEqual(alerts[0].value, {"target_seat": 2, "stage": "neighbor"})
        self.assertFalse(sends(self.m.tick(T0 + 20), "fall_alert"))        # 한 번만
        fx = self.m.tick(T0 + 30.0)
        self.assertEqual(len(sends(fx, "fall_alert")), 4)
        self.assertEqual(self.m.events[1].alert_stage, "all")

    def test_confirm_stops_alerts_and_cancels(self):
        self.start("fall", 2, 0)
        self.m.tick(T0 + 15)
        fx = self.m.on_confirm(1, T0 + 16, "instructor_console")
        self.assertEqual(sorted(c.to for c in sends(fx, "fall_alert_cancel")),
                         ["student_client_1", "student_client_3"])           # 알림 받은 옆자리만
        self.assertEqual(self.m.events[1].confirmed_by, "instructor_console")
        self.assertEqual(self.m.events[1].status, "active")             # confirm 은 상태가 아님
        self.assertFalse(sends(self.m.tick(T0 + 40), "fall_alert"))

    def test_student_fall_confirm(self):
        self.start("fall", 2, 0)
        self.m.tick(T0 + 15)
        fx = self.m.on_fall_confirm(2, T0 + 17, "student_client_1")
        self.assertEqual(self.m.events[1].confirmed_by, "student_client_1")
        self.assertTrue(sends(fx, "fall_alert_cancel"))

    def test_confirm_before_alert_sends_no_cancel(self):
        self.start("fall", 2, 0)
        fx = self.m.on_confirm(1, T0 + 3, "instructor_console")
        self.assertFalse(sends(fx, "fall_alert_cancel"))

    # ---- 만료 / 미복귀 ---------------------------------------------------
    def test_question_expires(self):
        self.start("question", 2, 0)
        self.start("question", 3, 10)
        fx = self.m.tick(T0 + 180)
        closed = [r for r in fx.saves if r["event_id"] == 1 and r["status"] == "closed"]
        self.assertEqual(closed[0]["close_reason"], "expired")
        self.assertEqual(self.queue(), {"active": 2, "waiting": []})

    def test_confirmed_question_does_not_expire(self):
        self.start("question", 2, 0)
        self.m.on_confirm(1, T0 + 5, "instructor_console")
        self.m.tick(T0 + 500)
        self.assertIn(1, self.m.events)

    def test_away_overdue_alert_once(self):
        self.start("away", 1, 0)
        fx = self.m.tick(T0 + 60)
        self.assertTrue(sends(fx, "event_update")[0].value["overdue"])
        self.assertFalse(sends(self.m.tick(T0 + 70), "event_update"))

    # ---- 추적 -----------------------------------------------------------
    def test_track_offset_only_for_active_seat_and_rate_limited(self):
        self.start("question", 2, 0)
        self.assertFalse(sends(self.m.on_track_offset(3, {"found": True, "dx": 0.1, "dy": 0}, T0 + 1)))
        fx = self.m.on_track_offset(2, {"found": True, "dx": 0.25, "dy": -0.1}, T0 + 1)
        self.assertEqual(sends(fx, "pan_tilt")[0].value, {"mode": "nudge", "dpan": 2.0, "dtilt": -0.8})
        self.assertFalse(sends(self.m.on_track_offset(2, {"found": True, "dx": 0.1, "dy": 0}, T0 + 1.05)))
        self.assertTrue(sends(self.m.on_track_offset(2, {"found": True, "dx": 0.1, "dy": 0}, T0 + 1.11)))
        self.assertFalse(sends(self.m.on_track_offset(2, {"found": False}, T0 + 2)))

    # ---- 장비 / 반 집계 --------------------------------------------------
    def test_node_offline_device_update(self):
        self.m.on_node_online("vision_service", True, T0)
        fx = self.m.on_node_online("vision_service", False, T0 + 7)
        self.assertEqual(sends(fx, "device_update")[0].value,
                         {"node": "vision_service", "online": False, "note": "감지 중단"})
        self.assertFalse(self.m.on_node_online("vision_service", False, T0 + 8))

    def test_camera_reconnect_gets_current_preset(self):
        self.start("question", 4, 0)
        fx = self.m.on_node_online("camera_controller", True, T0 + 1)
        self.assertEqual(sends(fx, "pan_tilt")[0].value, {"mode": "preset", "seat": 4, "pan": 120, "tilt": 85})

    def test_class_condition_ratio(self):
        self.m.tick(T0)
        self.m.on_condition_summary(1, {"focus_low": True, "posture_bad": False}, T0 + 1)
        self.m.on_condition_summary(2, {"focus_low": False, "posture_bad": True}, T0 + 2)
        self.m.on_condition_summary(3, {"focus_low": False, "posture_bad": True}, T0 + 3)
        self.m.on_condition_summary(4, {"focus_low": False, "posture_bad": False}, T0 + 4)
        fx = self.m.tick(T0 + 10)
        self.assertEqual(sends(fx, "class_condition")[0].value,
                         {"focus_low_ratio": 0.25, "posture_bad_ratio": 0.5, "reporting": 4})

    # ---- 재시작 복구 ----------------------------------------------------
    def test_restore_open_events(self):
        self.start("question", 2, 0)
        self.start("fall", 3, 1)
        from hub.event_manager import HelpEvent
        rows = [ev.to_row() for ev in self.m.events.values()]
        m2 = EventManager(self.cfg, 3, [HelpEvent(**r) for r in rows])
        self.assertEqual(m2.snapshot()["queue"], {"active": 2, "waiting": [1]})
        self.assertEqual(m2.next_id, 3)


if __name__ == "__main__":
    unittest.main()
