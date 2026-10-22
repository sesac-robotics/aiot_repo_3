import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_assistant import Receiver, nose_target
from fixed_camera.core import Person


class TargetTest(unittest.TestCase):
    def person(self, confidence=.9):
        return Person((.1,.1,.9,.9), [[.4,.3,confidence]]+[[0,0,0]]*16, None, .9)

    def test_single_person_nose(self):
        self.assertEqual(nose_target([self.person()]), (.4,.3))

    def test_multiple_people_rejected(self):
        self.assertIsNone(nose_target([self.person(),self.person()]))

    def test_no_person_or_hidden_nose(self):
        self.assertIsNone(nose_target([]))
        self.assertIsNone(nose_target([self.person(.1)]))


class StaleFrameWarningTest(unittest.TestCase):
    """시계 차이로 프레임을 버리면 조용히 넘기지 않고, WARN_S 에 한 번 개수와 차이를 알린다."""

    def receiver(self):
        r = Receiver.__new__(Receiver)               # 소켓을 열지 않고 경고 부분만 시험
        r.cam, r.stale, r.worst_age, r.warned_at = 1, 0, 0., 0.
        return r

    def test_warns_once_with_worst_age(self):
        r = self.receiver()
        with self.assertLogs('vision', 'WARNING') as cm:
            for age in (.9, 1.4, 1.1):
                r._drop_stale(age)
        self.assertEqual(len(cm.output), 1)
        self.assertIn('0.90', cm.output[0])         # 첫 경고 때까지의 최대 차이
        self.assertEqual(r.stale, 2)                 # 경고 뒤에 버린 2개는 다음 경고에 합산
