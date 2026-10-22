"""
event_manager 단위 테스트 (상세 설계 9장 "단위"): 가짜 시계로 사건을 순서대로 넣는다.
확인: 선점, FIFO, 15초/30초 알림, 만료, 중복 무시, confirm/resolve, 팬틸트 연동(preset/home/track, gen),
      녹화 구간(확정 ~ 종료), [수동보기] 좌석 보기, [알림문구] 상황별 explain_request·늦은 응답 거르기,
      [세부컨디션] 반 평균 세부 6개 비율·최소 학생 수

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
PT = "pantilt"                                        # [팬틸트]
REC = "recorder"                                      # [녹화]


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
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 2, "gen": 1})          # [팬틸트] gen
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 1, "seat": 2, "kind": 2})  # [팬틸트]
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
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 3, "gen": 2})          # [팬틸트] gen
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 2, "seat": 3, "kind": 1})  # [팬틸트]
        self.assertIn("preempt", [l[2] for l in fx.logs])

    def test_away_waits_behind_question(self):
        self.start("away", 1, 0)
        self.start("question", 2, 1)
        self.assertEqual(self.queue(), {"active": 2, "waiting": [1]})

    def test_dismiss_closes_as_false_alarm_and_cancels_alert(self):
        self.start("fall", 2, 0)
        self.start("question", 3, 1)
        self.m.tick(T0 + 16)                                                 # 옆자리 알림까지 간 쓰러짐
        fx = self.m.on_dismiss(1, T0 + 17, "instructor_console")
        closed = [s for s in fx.saves if s["event_id"] == 1][-1]
        self.assertEqual((closed["status"], closed["close_reason"]), ("closed", "false_alarm"))
        self.assertIn("dismiss", [l[2] for l in fx.logs])
        self.assertEqual(sorted(c.to for c in sends(fx, "fall_alert_cancel")),
                         ["student_client_1", "student_client_4"])
        self.assertEqual(self.queue(), {"active": 2, "waiting": []})         # 다음 대상으로
        self.assertFalse(self.m.on_dismiss(1, T0 + 18, "instructor_console"))   # 이미 닫힘

    def test_resolve_moves_to_next(self):
        self.start("question", 2, 0)
        self.start("fall", 3, 1)
        fx = self.m.on_resolve(2, T0 + 5, "instructor_console")
        self.assertEqual(self.queue(), {"active": 1, "waiting": []})
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 2, "gen": 3})          # [팬틸트] gen
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 3, "seat": 2, "kind": 2})  # [팬틸트]
        fx = self.m.on_resolve(1, T0 + 6, "instructor_console")
        self.assertEqual(self.queue(), {"active": None, "waiting": []})
        self.assertEqual(sends(fx, "home", PT)[0].value, {"gen": 4})                         # [팬틸트]
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": None, "gen": 4})       # [팬틸트] gen

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
        self.assertEqual(sorted(a.to for a in alerts), ["student_client_1", "student_client_4"])   # 2×2
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
                         ["student_client_1", "student_client_4"])           # 알림 받은 옆자리만
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

    # ---- 추적 ([팬틸트] nudge 시험 대체: 좌표를 그대로 팬틸트로, 지난 gen 은 버림) ----
    def test_track_obs_passes_only_current_gen(self):                               # [팬틸트]
        obs = {"frame_id": 7, "captured_at": T0 + 0.9, "status": "TRACKED", "cx": 0.6, "cy": 0.5}
        self.assertFalse(self.m.on_track_obs(dict(obs, gen=0), T0 + 1))             # 사건 없음
        self.start("question", 2, 0)                                                # gen 1
        self.assertFalse(self.m.on_track_obs(dict(obs, gen=0), T0 + 1))             # 지난 gen
        fx = self.m.on_track_obs(dict(obs, gen=1), T0 + 1)
        self.assertEqual(sends(fx, "track", PT)[0].value,
                         {"gen": 1, "cx": 0.6, "cy": 0.5, "captured_at": T0 + 0.9,
                          "frame_id": 7, "found": True})
        fx = self.m.on_track_obs(dict(obs, gen=1, status="LOST"), T0 + 1.1)
        self.assertFalse(sends(fx, "track", PT)[0].value["found"])

    def test_same_seat_kind_change_sends_new_preset(self):                          # [팬틸트]
        self.start("question", 2, 0)
        fx = self.start("fall", 2, 1)                       # 같은 좌석, 질문 → 쓰러짐
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 2, "seat": 2, "kind": 1})

    # ---- 장비 / 반 집계 --------------------------------------------------
    def test_node_offline_device_update(self):
        self.m.on_node_online("vision_service", True, T0)
        fx = self.m.on_node_online("vision_service", False, T0 + 7)
        self.assertEqual(sends(fx, "device_update")[0].value,
                         {"node": "vision_service", "online": False, "note": "감지 중단"})
        self.assertEqual(sends(fx, "vision_online", PT)[0].value, {"online": False})   # [팬틸트]
        self.assertFalse(self.m.on_node_online("vision_service", False, T0 + 8))

    def test_vision_reconnect_gets_current_target(self):                            # [팬틸트] camera_reconnect 대체
        self.start("question", 4, 0)
        fx = self.m.on_node_online("vision_service", True, T0 + 1)
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 4, "gen": 1})
        self.assertEqual(sends(fx, "vision_online", PT)[0].value, {"online": True})

    def test_pantilt_safe_updates_device(self):                                     # [팬틸트]
        state = {"state": 1, "pan": 81, "tilt": 90, "gen": 0, "error": 0}
        fx = self.m.on_pantilt_state(state, T0)
        self.assertTrue(sends(fx, "device_update")[0].value["online"])
        self.assertFalse(self.m.on_pantilt_state(dict(state, state=3), T0 + 1))     # 정상 → 정상: 알림 없음
        fx = self.m.on_pantilt_state(dict(state, state=7, error=1), T0 + 2)         # SAFE
        self.assertEqual(sends(fx, "device_update")[0].value,
                         {"node": PT, "online": False, "note": "추적 중단 (알림은 계속)"})
        self.assertEqual(self.m.pan_tilt["state"], 7)

    def test_preset_register_flow(self):                                            # [팬틸트]
        fx = self.m.on_pan_tilt_jog(1, -1, T0)
        self.assertEqual(sends(fx, "jog", PT)[0].value, {"dpan": 1, "dtilt": -1})
        fx = self.m.on_preset_save(2, T0 + 1)
        self.assertEqual(sends(fx, "save", PT)[0].value, {"seat": 2})
        fx = self.m.on_preset_saved({"seat": 2, "pan": 83, "tilt": 88}, T0 + 2)
        self.assertEqual(sends(fx, "seat_preset", "db")[0].value, {"seat": 2, "has_preset": 1})
        self.assertEqual(sends(fx, "preset_saved")[0].value, {"seat": 2, "pan": 83, "tilt": 88})

    def test_class_condition_ratio(self):
        self.m.tick(T0)
        self.m.on_condition_summary(1, {"focus_low": True, "posture_bad": False}, T0 + 1)
        self.m.on_condition_summary(2, {"focus_low": False, "posture_bad": True}, T0 + 2)
        self.m.on_condition_summary(3, {"focus_low": False, "posture_bad": True}, T0 + 3)
        self.m.on_condition_summary(4, {"focus_low": False, "posture_bad": False}, T0 + 4)
        fx = self.m.tick(T0 + 10)
        v = sends(fx, "class_condition")[0].value
        self.assertEqual({k: v[k] for k in ("focus_low_ratio", "posture_bad_ratio", "reporting")},
                         {"focus_low_ratio": 0.25, "posture_bad_ratio": 0.5, "reporting": 4})
        self.assertTrue(all(x is None for x in v["detail"].values()))   # 옛 클라이언트: 세부 키 없음 → null

    # ---- [세부컨디션] ----------------------------------------------------
    def detail_summary(self, seat, t, **on):
        keys = ("drowsy", "yawn", "not_forward", "forward_lean", "head_tilt", "shoulder_tilt")
        v = {k: bool(on.get(k)) for k in keys}
        v["focus_low"] = v["drowsy"] or v["yawn"] or v["not_forward"]
        v["posture_bad"] = v["forward_lean"] or v["head_tilt"] or v["shoulder_tilt"]
        self.m.on_condition_summary(seat, v, T0 + t)

    def test_class_condition_detail_ratio(self):
        self.m.tick(T0)
        self.detail_summary(1, 1, drowsy=True, yawn=True)
        self.detail_summary(2, 2, drowsy=True)
        self.detail_summary(3, 3, forward_lean=True)
        self.m.on_condition_summary(4, {"focus_low": False, "posture_bad": False}, T0 + 4)  # 옛 클라이언트
        d = sends(self.m.tick(T0 + 10), "class_condition")[0].value["detail"]
        # 분모 = 세부 키를 보낸 3명 (4번 좌석 제외)
        self.assertEqual(d, {"drowsy": 0.67, "yawn": 0.33, "not_forward": 0.0,
                             "forward_lean": 0.33, "head_tilt": 0.0, "shoulder_tilt": 0.0})

    def test_class_condition_detail_min_students(self):
        cfg = copy.deepcopy(CONTROL_DEFAULTS)
        cfg["condition"]["detail_min_students"] = 3
        self.m = EventManager(cfg)
        self.m.tick(T0)
        self.detail_summary(1, 1, drowsy=True)
        self.detail_summary(2, 2)
        v = sends(self.m.tick(T0 + 10), "class_condition")[0].value
        self.assertEqual(v["reporting"], 2)
        self.assertTrue(all(x is None for x in v["detail"].values()))   # 2명 < 3명 → null
        self.assertEqual(v["focus_low_ratio"], 0.5)                      # 묶음은 그대로

    def test_class_condition_detail_one_student(self):                   # 기본값 1명 (테스트 중)
        self.m.tick(T0)
        self.detail_summary(1, 1, drowsy=True)
        d = sends(self.m.tick(T0 + 10), "class_condition")[0].value["detail"]
        self.assertEqual((d["drowsy"], d["yawn"]), (1.0, 0.0))

    # ---- 재시작 복구 ----------------------------------------------------
    def test_restore_open_events(self):
        self.start("question", 2, 0)
        self.start("fall", 3, 1)
        from hub.event_manager import HelpEvent
        rows = [ev.to_row() for ev in self.m.events.values()]
        m2 = EventManager(self.cfg, 3, [HelpEvent(**r) for r in rows])
        self.assertEqual(m2.snapshot()["queue"], {"active": 2, "waiting": [1]})
        self.assertEqual(m2.next_id, 3)
        fx = m2.start(T0 + 2)                                                       # [팬틸트] 재시작 후 팬틸트 복구
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 1, "seat": 3, "kind": 1})

        self.assertEqual([s.value["event_id"] for s in sends(fx, "start", REC)], [2, 1])   # [녹화] 열린 이벤트 녹화 재개

    # ---- [녹화] 녹화 구간 = 확정 ~ 완료·오감지·만료·복귀 -------------------------
    def test_record_starts_on_detect_for_every_event(self):
        fx1 = self.start("question", 2, 0)
        fx2 = self.start("away", 4, 1)                  # 대기열로 가도 녹화는 확정 시각부터
        self.assertEqual(sends(fx1, "start", REC)[0].value, {"event_id": 1, "seat": 2, "kind": "question"})
        self.assertEqual(sends(fx2, "start", REC)[0].value, {"event_id": 2, "seat": 4, "kind": "away"})
        self.assertFalse(self.start("question", 2, 2))  # 중복 사건은 새 녹화 없음

    def test_record_not_stopped_by_confirm_preempt_or_end_without_close(self):
        self.start("question", 2, 0)
        fxs = [self.start("fall", 3, 1),                                   # 선점
               self.m.on_confirm(1, T0 + 2, "instructor_console"),
               self.m.on_incident("question", 2, "end", T0 + 3),          # 손 내림 (닫지 않음)
               self.m.on_incident("fall", 3, "end", T0 + 4)]              # 일어남 (닫지 않음)
        self.assertEqual([s for fx in fxs for s in sends(fx, "stop", REC)], [])

    def test_record_stops_on_every_close(self):
        self.start("question", 1, 0)
        self.start("fall", 2, 1)
        self.start("away", 3, 2)
        self.start("question", 4, 3)
        cases = [(self.m.on_resolve(1, T0 + 10, "instructor_console"), 1),
                 (self.m.on_dismiss(2, T0 + 11, "instructor_console"), 2),
                 (self.m.on_incident("away", 3, "end", T0 + 12), 3),
                 (self.m.tick(T0 + 3 + self.cfg["timers"]["question_expire_s"]), 4)]   # 만료
        for fx, eid in cases:
            self.assertEqual([s.value for s in sends(fx, "stop", REC)], [{"event_id": eid}])



class ExplainSituationTest(unittest.TestCase):                           # [알림문구]
    """상황이 바뀔 때마다 vision_service 에 explain_request {situation} 를 보내는지."""

    def setUp(self):
        self.m = EventManager(copy.deepcopy(CONTROL_DEFAULTS))

    def start(self, kind, seat, t):
        return self.m.on_incident(kind, seat, "start", T0 + t, source="vision_service:1")

    def req(self, fx):
        r = sends(fx, "explain_request")
        self.assertEqual(len(r), 1, fx.sends)
        return r[0].value

    def test_detect_active(self):
        v = self.req(self.start("question", 2, 0))
        self.assertEqual((v["situation"], v["status"], v["queue_pos"], v["seq"]), ("detect", "active", 0, 1))
        self.assertNotIn("preempted_kind", v)

    def test_detect_preempts(self):
        self.start("question", 2, 0)
        v = self.req(self.start("fall", 3, 1))
        self.assertEqual((v["situation"], v["status"], v["preempted_kind"], v["preempted_seat"]),
                         ("detect", "active", "question", 2))
        self.assertEqual(v["waiting_count"], 1)

    def test_detect_waiting_has_queue_pos(self):
        self.start("fall", 3, 0)
        self.start("question", 2, 1)
        v = self.req(self.start("question", 4, 2))
        self.assertEqual((v["status"], v["queue_pos"], v["waiting_count"]), ("waiting", 2, 2))

    def test_fall_neighbor_and_all(self):
        self.start("fall", 3, 0)
        v = self.req(self.m.tick(T0 + 15))
        self.assertEqual((v["situation"], v["alert_stage"], v["elapsed_s"], v["seq"]), ("fall_neighbor", "neighbor", 15.0, 2))
        v = self.req(self.m.tick(T0 + 30))
        self.assertEqual((v["situation"], v["alert_stage"]), ("fall_all", "all"))

    def test_no_fall_alert_explain_after_confirm(self):
        self.start("fall", 3, 0)
        self.m.on_confirm(1, T0 + 5, "instructor_console")
        self.assertEqual(sends(self.m.tick(T0 + 31), "explain_request"), [])

    def test_fall_risen_only_before_confirm(self):
        self.start("fall", 3, 0)
        v = self.req(self.m.on_incident("fall", 3, "end", T0 + 5))
        self.assertEqual(v["situation"], "fall_risen")
        self.start("fall", 3, 6)                                           # 다시 쓰러짐
        self.m.on_confirm(1, T0 + 7, "instructor_console")
        self.assertEqual(sends(self.m.on_incident("fall", 3, "end", T0 + 8), "explain_request"), [])

    def test_hand_down_and_again(self):
        self.start("question", 2, 0)
        self.assertEqual(self.req(self.m.on_incident("question", 2, "end", T0 + 3))["situation"], "hand_down")
        self.assertEqual(self.req(self.start("question", 2, 4))["situation"], "again")

    def test_away_overdue_once(self):
        self.start("away", 4, 0)
        v = self.req(self.m.tick(T0 + 60))
        self.assertEqual(v["situation"], "away_overdue")
        self.assertEqual(sends(self.m.tick(T0 + 61), "explain_request"), [])

    def test_stale_explain_text_ignored(self):
        self.start("fall", 3, 0)                                           # seq 1 (detect)
        self.m.tick(T0 + 15)                                               # seq 2 (fall_neighbor)
        fx = self.m.on_explain_text(1, "옛 문구", "gemini", T0 + 16, seq=1)
        self.assertFalse(fx)
        self.assertIsNone(self.m.events[1].explain_text)
        fx = self.m.on_explain_text(1, "새 문구", "ollama", T0 + 16, seq=2)
        self.assertEqual(sends(fx, "event_update")[0].value["explain_text"], "새 문구")
        self.assertEqual(sends(fx, "event_update")[0].value["explain_situation"], "fall_neighbor")

    def test_explain_text_without_seq_still_accepted(self):              # 예전 vision 과도 호환
        self.start("question", 2, 0)
        fx = self.m.on_explain_text(1, "문구", "default", T0 + 1)
        self.assertEqual(sends(fx, "event_update")[0].value["explain_text"], "문구")



class ManualViewTest(unittest.TestCase):                                 # [수동보기]
    """강사 "1번 좌석 보여줘": 이벤트 없을 때만, 60초 뒤 원위치, 새 이벤트가 오면 넘김."""

    def setUp(self):
        self.m = EventManager(copy.deepcopy(CONTROL_DEFAULTS))
        self.C = "instructor_console"

    def test_view_when_idle_moves_without_led(self):
        fx, r = self.m.on_view_request(1, T0, self.C)
        self.assertEqual((r["ok"], r["seat"], r["hold_s"]), (True, 1, 60.0))
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 1, "seat": 1, "kind": 0})       # LED 끔
        self.assertEqual(sends(fx, "track_target")[0].value, {"seat": 1, "gen": 1})
        self.assertEqual(sends(fx, "view_state")[0].value["seat"], 1)
        self.assertEqual(sends(fx, "explain_request"), [])                    # 이벤트 아님
        self.assertEqual(fx.saves, [])                                        # DB 이벤트 아님

    def test_refused_while_tracking(self):
        self.m.on_incident("question", 2, "start", T0)
        fx, r = self.m.on_view_request(1, T0 + 1, self.C)
        self.assertEqual((r["ok"], r["reason"], r["tracking"]["seat"]), (False, "tracking", 2))
        self.assertFalse(fx)                                                  # 아무것도 안 움직임

    def test_timeout_goes_home(self):
        self.m.on_view_request(3, T0, self.C)
        self.assertEqual(sends(self.m.tick(T0 + 59), "home", PT), [])
        fx = self.m.tick(T0 + 60)
        self.assertEqual(len(sends(fx, "home", PT)), 1)
        self.assertEqual(sends(fx, "view_state")[0].value["why"], "timeout")
        self.assertIsNone(self.m.view)

    def test_new_event_takes_over(self):
        self.m.on_view_request(1, T0, self.C)
        fx = self.m.on_incident("fall", 4, "start", T0 + 5)
        self.assertEqual(sends(fx, "preset", PT)[0].value, {"gen": 2, "seat": 4, "kind": 1})
        self.assertEqual(sends(fx, "view_state")[0].value["why"], "event")
        self.assertIsNone(self.m.view)
        self.m.on_resolve(1, T0 + 10, self.C)                                 # 이벤트가 끝나도 수동 보기로 안 돌아감
        self.assertIsNone(self.m.view)

    def test_home_request_and_repeat(self):
        self.m.on_view_request(1, T0, self.C)
        fx, r = self.m.on_view_request(2, T0 + 10, self.C)                    # 다른 좌석으로 바꾸기
        self.assertEqual(sends(fx, "preset", PT)[0].value["seat"], 2)
        self.assertEqual(self.m.view["until"], T0 + 70)
        fx, r = self.m.on_view_request(None, T0 + 20, self.C)                 # 원위치
        self.assertTrue(r["home"])
        self.assertEqual(len(sends(fx, "home", PT)), 1)
        fx, r = self.m.on_view_request(None, T0 + 21, self.C)                 # 이미 원위치
        self.assertFalse(fx)

    def test_bad_seat_and_pantilt_safe(self):
        self.assertEqual(self.m.on_view_request(9, T0, self.C)[1]["reason"], "bad_seat")
        self.m.on_pantilt_state({"state": 7, "pan": 0, "tilt": 0, "gen": 0, "error": 1}, T0)
        self.assertEqual(self.m.on_view_request(1, T0, self.C)[1]["reason"], "pantilt_off")


if __name__ == "__main__":
    unittest.main()