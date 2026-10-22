import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixed_camera.core import Person, SeatMatcher, hand_state
from fixed_camera.config import Enrollment, validate, registration, save, load
from fixed_camera.decision_demo import DecisionDemo, Evidence
from fixed_camera.seat_report import SeatReport


def person(x=.3, y=.5, tid=7, up=False, shoulders=True):
    k = [[0., 0., 0.] for _ in range(17)]
    k[5] = [x-.04,y,.9 if shoulders else .1]
    k[6] = [x+.04,y,.9 if shoulders else .1]
    k[9] = [x-.05,y-.20 if up else y+.15,.9]
    k[10] = [x+.05,y+.15,.9]
    return Person((x-.1,y-.2,x+.1,y+.4), k, tid, .9)


def cfg(two=False):
    c = {'version':1,'camera_id':1,'aspect':4/3,'seats':{
        '1':{'anchor':[.3,.5],'scale':.08*4/3,'radius':.15}}}
    if two:c['seats']['2']={'anchor':[.7,.5],'scale':.08*4/3,'radius':.15}
    return c


class MatchingTests(unittest.TestCase):
    def test_auto_match_and_lean(self):
        m=SeatMatcher(cfg())
        self.assertIsNone(m.observe([person()],0)[0]['present'])
        self.assertTrue(m.observe([person()],.9)[0]['present'] is None) # continuity gap (> gap_s .8) reset
        for t in (1.,1.1,1.2,1.3,1.4,1.5,1.6):row=m.observe([person()],t)[0]
        self.assertIs(row['present'],True)
        self.assertIs(m.observe([person(x=.34,up=True)],1.7)[0]['hand_up'],True)

    def test_two_close_people_are_unknown(self):
        """서로 다른 두 사람이 한 좌석 범위 안에 비슷하게 가까우면 '모름' (어깨 가운데가 어깨 폭의 절반쯤 떨어짐)"""
        m=SeatMatcher(cfg(),confirm_s=0)
        row=m.observe([person(x=.285,tid=1),person(x=.315,tid=2)],1)[0]
        self.assertIsNone(row['present'])
        self.assertEqual(row['reason'],'AMBIGUOUS')

    def test_same_person_detected_twice_is_one(self):
        """YOLO 가 같은 사람을 두 번 잡은 것(같은 어깨 위치)은 한 명으로 합친다 (10/22 실물 시험 3·4번)"""
        m=SeatMatcher(cfg(),confirm_s=0)
        dup=person(x=.302,tid=2); dup.confidence=.36
        row=m.observe([person(tid=1),dup],1)[0]
        self.assertIs(row['present'],True)
        self.assertEqual(row['track_id'],1)                      # 신뢰도 높은 쪽

    def test_id_change_in_place_keeps_presence(self):
        """같은 자리에서 끊김 없이 추적 ID 만 바뀌면 '확인 중'으로 돌아가지 않는다"""
        m=SeatMatcher(cfg())
        for t in (0, .1, .2, .3, .4, .5, .6, .7):
            row=m.observe([person(tid=1)],t)[0]
        self.assertIs(row['present'],True)
        self.assertIs(m.observe([person(tid=9)],.8)[0]['present'],True)

    def test_missing_shoulders_is_not_vacancy(self):
        row=SeatMatcher(cfg()).observe([person(shoulders=False)],1)[0]
        self.assertIsNone(row['present'])
        self.assertEqual(row['reason'],'OCCLUDED_OR_UNMATCHED')

    def test_empty_and_unrelated_person(self):
        row=SeatMatcher(cfg()).observe([person(x=.8)],1)[0]
        self.assertIs(row['present'],False)

    def test_seats_not_renumbered_when_empty(self):
        m=SeatMatcher(cfg(True),confirm_s=0)
        rows=m.observe([person(x=.7)],1)
        self.assertIs(rows[0]['present'],False)
        self.assertIs(rows[1]['present'],True)
        self.assertEqual(rows[1]['seat'],2)

    def test_no_id_is_unknown(self):
        self.assertEqual(SeatMatcher(cfg()).observe([person(tid=None)],1)[0]['reason'],'NO_TRACK_ID')

    def test_faint_raised_wrist_counts(self):
        """뒷줄처럼 작게 찍혀 든 손목 신뢰도가 0.4 여도 손 듦 (어깨는 0.5 기준 그대로)"""
        p = person(up=True)
        p.keypoints[9][2] = .4
        self.assertIs(hand_state(p, 4/3)[0], True)
        self.assertIsNone(hand_state(p, 4/3, wrist_threshold=.5)[0])     # 예전 기준이면 '모름'

    def test_one_raised_arm_with_other_missing(self):
        p=person(up=True)
        p.keypoints[10][2]=.1
        self.assertIs(hand_state(p,4/3)[0],True)
        p.keypoints[9][1]=.7
        self.assertIsNone(hand_state(p,4/3)[0])

    def test_registration_persistence_and_aspect(self):
        entry=registration([(.3,.5,.1)]*10,4/3)
        c=cfg(); c['seats']['1']=entry
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'seats.json';save(p,c)
            self.assertEqual(load(p),c)
        with self.assertRaises(ValueError):validate(c,16/9)
        with self.assertRaises(ValueError):registration([(.3,.5,.1),(.7,.5,.1)]*5,4/3)


def room():
    """2×2 교실: 앞줄(1·2번)은 카메라에 가까워 크고 아래, 뒷줄(3·4번)은 작고 위"""
    def seat(x, y, sw):
        scale = sw*4/3
        return {'anchor': [x, y], 'scale': scale, 'radius': min(.18, max(.055, scale*.9))}
    return {'version': 1, 'camera_id': 1, 'aspect': 4/3, 'seats': {
        '1': seat(.30, .72, .12), '2': seat(.70, .72, .12), '3': seat(.36, .46, .07), '4': seat(.64, .46, .07)}}


def student(x, y, sw, tid, up=False):
    k = [[0., 0., 0.] for _ in range(17)]
    k[5], k[6] = [x-sw/2, y, .9], [x+sw/2, y, .9]
    k[9] = [x-sw/2, y-2*sw if up else y+sw, .9]
    k[10] = [x+sw/2, y+sw, .9]
    return Person((x-sw, y-1.5*sw, x+sw, y+3*sw), k, tid, .9)


EVERYONE = [student(.30, .72, .12, 1), student(.70, .72, .12, 2), student(.36, .46, .07, 3), student(.64, .46, .07, 4)]


class FourSeatTests(unittest.TestCase):
    def rows(self, people, times, **kw):
        m = SeatMatcher(room(), **kw)
        for t in times:
            rows = m.observe(people, t)
        return {r['seat']: r for r in rows}

    def test_all_four_present(self):
        rows = self.rows(EVERYONE, [i/15 for i in range(30)])
        self.assertEqual([rows[s]['present'] for s in (1, 2, 3, 4)], [True]*4)

    def test_front_student_standing_is_not_back_seat(self):
        """3번이 가려진 채 1번이 손 들며 일어나도, 3번 손들기로 잡지 않는다 (예전: 3번 질문이 생김)"""
        people = [student(.33, .52, .11, 1, up=True), EVERYONE[1], EVERYONE[3]]
        rows = self.rows(people, [i/15 for i in range(30)])
        self.assertIsNot(rows[3]['present'], True)
        self.assertIsNot(rows[3]['hand_up'], True)

    def test_slow_analysis_still_confirms(self):
        """분석이 1.6 fps 여도 좌석이 '확인 중'에 머물지 않는다 (예전: 0.5초 간격이라 영영 MATCH_PENDING)"""
        rows = self.rows(EVERYONE, [i*.62 for i in range(10)])
        self.assertEqual([rows[s]['present'] for s in (1, 2, 3, 4)], [True]*4)

    def test_turned_sideways_still_matches(self):
        """옆으로 돌아 어깨 폭이 등록 때의 35%여도 제자리면 '있음' (10/22 실물 시험의 4번 좌석)"""
        people = EVERYONE[:3] + [student(.64, .46, .07*.35, 4)]
        rows = self.rows(people, [i/15 for i in range(30)])
        self.assertIs(rows[4]['present'], True)

    def test_small_back_seat_tolerates_small_shift(self):
        """멀어서 작게 찍힌 뒷줄 학생이 앉은 채 0.05 옮겨도 '있음' (10/22 시험 4번: 반경 0.055면 모름 38%)"""
        cfg = room()
        cfg['seats']['4'].update(scale=.053, radius=.055)
        people = EVERYONE[:3] + [student(.59, .46, .04, 4)]
        m = SeatMatcher(cfg)
        for t in [i/15 for i in range(30)]:
            rows = {r['seat']: r for r in m.observe(people, t)}
        self.assertIs(rows[4]['present'], True)
        m = SeatMatcher(cfg, min_radius=0)                                # 예전 동작: 범위 밖
        for t in [i/15 for i in range(30)]:
            rows = {r['seat']: r for r in m.observe(people, t)}
        self.assertIsNot(rows[4]['present'], True)

    def test_corner_of_neighbor_box_does_not_hide_empty_seat(self):
        """뒷줄 학생 상자가 비어 있는 앞줄 좌석 범위에 모서리만 걸치면 '빔' (10/22 2m 시험 2번 자리비움)"""
        neighbor = student(.64, .46, .07, 4)
        neighbor.box = (.55, .35, .66, .70)                       # 2번 범위(가로 0.61~0.79) 왼쪽 끝에만 걸침
        rows = self.rows([EVERYONE[0], EVERYONE[2], neighbor], [i/15 for i in range(30)])
        self.assertIs(rows[2]['present'], False)

    def test_front_student_head_hiding_back_seat_is_unknown(self):
        """자기 좌석의 앞줄 학생이라도 머리가 비어 보이는 뒷줄 좌석 범위를 덮으면 비었다고 단정하지 않는다"""
        front = student(.36, .62, .12, 1)                         # 1번 좌석에 맞는 위치
        for i in range(5):                                       # 코·눈·귀가 3번 범위(y 0.37~0.52) 안
            front.keypoints[i] = [.36 + (i - 2)*.03, .44, .9]
        rows = self.rows([front, EVERYONE[1], EVERYONE[3]], [i/15 for i in range(30)])
        self.assertIs(rows[1]['present'], True)
        self.assertIsNone(rows[3]['present'])

    def test_seated_front_box_does_not_hide_empty_back_seat(self):
        """앞줄 학생 상자가 뒷줄 좌석 범위를 덮어도 머리·어깨가 비켜 있으면 '빔'
        (10/22 시험: 2번 상자가 비어 있는 4번 범위의 34~68% 를 덮어 4번 자리비움이 영영 '모름')"""
        front = student(.36, .62, .12, 1)                         # 상자 y 0.44~0.98, 머리 점 없음
        rows = self.rows([front, EVERYONE[1], EVERYONE[3]], [i/15 for i in range(30)])
        self.assertIs(rows[1]['present'], True)
        self.assertIs(rows[3]['present'], False)
        rows = self.rows([front, EVERYONE[1], EVERYONE[3]], [i/15 for i in range(30)], cover_by='box')
        self.assertIsNone(rows[3]['present'])                     # 예전 규칙: 상자 전체 = 가림

    def test_unseated_person_box_still_hides_back_seat(self):
        """좌석에 안 맞는 사람(서 있음·지나감)은 상자 전체를 가림으로 본다"""
        passer = Person((.26, .38, .46, .95), [[0., 0., 0.] for _ in range(17)], 9, .9)   # 어깨 안 보임
        rows = self.rows([passer, EVERYONE[0], EVERYONE[1], EVERYONE[3]], [i/15 for i in range(30)])
        self.assertIsNone(rows[3]['present'])

    def test_seat_windows_do_not_overlap(self):
        m = SeatMatcher(room())
        for s, r in m.radius.items():
            for t, q in m.radius.items():
                if s < t:
                    gap = math.hypot((m.seats[s]['anchor'][0]-m.seats[t]['anchor'][0])*m.aspect,
                                     m.seats[s]['anchor'][1]-m.seats[t]['anchor'][1])
                    self.assertLessEqual(r + q, gap + 1e-9)


class SeatReportTests(unittest.TestCase):
    def test_summary_names_the_reason_for_unknown(self):
        r = SeatReport(every_s=1.)
        m = SeatMatcher(room())
        line = None
        for i in range(20):                                        # 3번 학생은 추적 ID 없음 → 모름
            people = EVERYONE[:2] + [student(.36, .46, .07, None)] + EVERYONE[3:]
            line = r.add(m.observe(people, i/15), i/15) or line
        self.assertIn('3번 모름', line)
        self.assertIn('NO_TRACK_ID', line)
        self.assertIn('1번 있음', line)


class EnrollmentTests(unittest.TestCase):
    def feed(self, frames):
        """frames = [(time, sample or None)] → 마지막 상태"""
        e = Enrollment(0.)
        state = None
        for t, s in frames:
            state = e.add(s, t)
            if state != 'collecting':
                break
        return state, e

    def test_brief_dropout_is_tolerated(self):
        """뒷줄 학생 어깨 점이 한두 프레임 깜빡여도 등록된다 (예전: 바로 취소)"""
        frames = [(i/10, None if i in (7, 8) else (.3, .5, .1)) for i in range(25)]
        self.assertEqual(self.feed(frames)[0], 'done')

    def test_slow_analysis_waits_for_samples(self):
        """3 fps 여도 표본 8개가 모일 때까지 기다린다 (예전: 2초에 6개라 실패)"""
        frames = [(i/3, (.3, .5, .1)) for i in range(15)]
        state, e = self.feed(frames)
        self.assertEqual(state, 'done')
        self.assertGreaterEqual(len(e.samples), 8)

    def test_long_loss_cancels_with_reason(self):
        frames = [(i/10, (.3, .5, .1) if i < 5 else None) for i in range(20)]
        state, e = self.feed(frames)
        self.assertEqual(state, 'cancelled')
        self.assertIn('lost', e.reason)

    def test_too_slow_times_out_with_reason(self):
        frames = [(i*1.0, (.3, .5, .1)) for i in range(8)]        # 1 fps: 6초에 7개
        state, e = self.feed(frames)
        self.assertEqual(state, 'cancelled')
        self.assertIn('too slow', e.reason)


class DecisionTests(unittest.TestCase):
    def setUp(self):self.demo=DecisionDemo(question_s=.6,away_s=1,return_s=.4)
    def feed(self,t,p=True,h=False,seat=1):
        return self.demo.process({'session':'s','camera_id':1,'frame_id':int(t*10)+1,
            'seats':[{'seat':seat,'present':p,'hand_up':h}]},t)
    def test_initial_empty_does_not_create_away(self):
        for n in range(30):self.feed(n/10,False,None)
        self.assertFalse(self.demo.events)
    def test_away_and_return(self):
        self.feed(0)
        changes=[]
        for n in range(1,16):changes+=self.feed(n/10,False,None)
        self.assertEqual([e['kind'] for e in changes],['away'])
        for n in range(16,24):self.feed(n/10)
        self.assertFalse(self.demo.events)
    def test_unknown_not_absent_and_long_unknown_resets(self):
        self.feed(0)
        for n in range(1,8):self.feed(n/10,False,None)
        for n in range(8,25):self.feed(n/10,None,None)
        for n in range(25,30):self.feed(n/10,False,None)
        self.assertFalse(self.demo.events)
    def test_hand_down_preserves_question_and_complete_needs_rearm(self):
        for n in range(10):self.feed(n/10,True,True)
        self.assertIn((1,'question'),self.demo.events)
        self.feed(1,True,False)
        self.assertTrue(self.demo.events[1,'question']['hand_down'])
        self.demo.close(1,'question',1.1)
        for n in range(12,25):self.feed(n/10,True,True)
        self.assertFalse(self.demo.events)
        for n in range(25,34):self.feed(n/10,True,False)
        for n in range(34,44):self.feed(n/10,True,True)
        self.assertIn((1,'question'),self.demo.events)
    def test_simultaneous_questions_order_and_duplicate_frame(self):
        for n in range(10):
            obs={'session':'s','camera_id':1,'frame_id':n,
                'seats':[{'seat':s,'present':True,'hand_up':True} for s in (1,2)]}
            self.demo.process(obs,n/10)
        self.assertEqual([e['seat'] for e in self.demo.queue()],[1,2])
        self.assertEqual(self.demo.process(obs,1),[])
    def test_pipeline_seat_to_question_to_away(self):
        m=SeatMatcher(cfg(),confirm_s=.2)
        out=[]
        for n in range(45):
            rows=m.observe([person(up=True)] if n<20 else [],n/10)
            out+=self.demo.process({'session':'p','camera_id':1,'frame_id':n,'seats':rows},n/10)
        self.assertEqual([e['kind'] for e in out],['question','away'])
        self.assertEqual(self.demo.queue()[0]['kind'],'question')
    def test_long_gap_does_not_accumulate(self):
        e=Evidence()
        self.assertFalse(e.update(True,0,1))
        self.assertFalse(e.update(True,5,1))
        self.assertFalse(e.update(True,5.1,1))
    def test_help_without_cv2_or_weights(self):
        p=Path(__file__).resolve().parents[1]/'run_fixed_vision.py'
        r=subprocess.run([sys.executable,str(p),'--help'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('--calibrate',r.stdout)

if __name__=='__main__':unittest.main()
