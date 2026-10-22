import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_assistant import nose_target
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
