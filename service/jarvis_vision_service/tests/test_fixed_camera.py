import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixed_camera.core import Person, SeatMatcher, hand_state
from fixed_camera.config import validate, registration, save, load
from fixed_camera.decision_demo import DecisionDemo, Evidence


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
        self.assertTrue(m.observe([person()],.7)[0]['present'] is None) # continuity gap reset
        for t in (.8,.9,1.,1.1,1.2,1.3,1.4):row=m.observe([person()],t)[0]
        self.assertIs(row['present'],True)
        self.assertIs(m.observe([person(x=.34,up=True)],1.5)[0]['hand_up'],True)

    def test_duplicate_people_are_unknown(self):
        m=SeatMatcher(cfg(),confirm_s=0)
        row=m.observe([person(tid=1),person(tid=2)],1)[0]
        self.assertIsNone(row['present'])
        self.assertEqual(row['reason'],'AMBIGUOUS')

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
