"""Integration boundaries: binary observations -> central timing -> motor effects."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.control_config import CONTROL_DEFAULTS
from hub.event_manager import EventManager
from hub.video_relay import VideoRelay
from common.protocol import make_message, encode, parse_message, MessageError, VIDEO_HEADER


class IntegrationTest(unittest.TestCase):
    def setUp(self):
        self.manager = EventManager(copy.deepcopy(CONTROL_DEFAULTS))
        self.fid = 0

    def obs(self, t, present=True, hand=True, session='test'):
        self.fid += 1
        v = dict(session=session, frame_id=self.fid, captured_at=t, processed_at=t,
                 seats=[dict(seat=1, present=present, hand_up=hand)])
        msg = parse_message(encode(make_message('vision_service', self.fid, 'seat_obs', value=v)))
        return self.manager.on_seat_obs(msg['value'], t)

    def test_question_preset_and_generation(self):
        sends = []
        for i in range(23):
            sends.extend(self.obs(1000+i*.1).sends)
        self.assertEqual(len(self.manager.events), 1)
        preset = [s for s in sends if s.type == 'preset']
        self.assertEqual(preset[0].value, dict(gen=1, seat=1, kind=2))
        self.assertEqual(sum(s.type == 'track_target' for s in sends), 1)

    def test_unknown_is_not_away(self):
        self.obs(1000, hand=False)
        for i in range(130):
            self.obs(1000.1+i*.1, present=None, hand=None)
        self.assertFalse(self.manager.events)

    def test_away_after_seen_and_return(self):
        self.obs(1000, hand=False)
        for i in range(103):
            self.obs(1000.1+i*.1, present=False, hand=None)
        self.assertEqual(self.manager.active.kind, 'away')
        for i in range(23):
            self.obs(1010.4+i*.1, hand=False)
        self.assertIsNone(self.manager.active)

    def test_initial_empty_is_not_away(self):
        for i in range(130):
            self.obs(1000+i*.1, present=False, hand=None)
        self.assertFalse(self.manager.events)

    def test_long_gap_resets_hand_hold(self):
        for i in range(15):
            self.obs(1000+i*.1)
        self.obs(1005)
        self.assertFalse(self.manager.events)

    def test_duplicate_frames_and_stale_rejected(self):
        self.obs(1000)
        frame = self.manager._obs_frame
        v = dict(session='test', frame_id=frame, captured_at=1000, seats=[])
        self.assertFalse(self.manager.on_seat_obs(v, 1000.1))
        self.assertFalse(self.manager.on_seat_obs(v, 1002))

    def test_lost_without_coordinates_and_old_generation(self):
        self.manager.on_incident('question', 1, 'start', 1000)
        lost = dict(gen=1, frame_id=2, captured_at=1000.1, status='LOST')
        v = parse_message(encode(make_message('vision_service', 1, 'track_obs', 1, lost)))['value']
        fx = self.manager.on_track_obs(v, 1000.1)
        self.assertFalse(fx.sends[0].value['found'])
        self.assertFalse(self.manager.on_track_obs({**v, 'gen': 0}, 1000.2))

    def test_invalid_observation_rejected(self):
        v = dict(session='test', frame_id=1, captured_at=1000, processed_at=1000,
                 seats=[dict(seat=1, present=1, hand_up=True)])
        with self.assertRaises(MessageError):
            parse_message(encode(make_message('vision_service', 1, 'seat_obs', value=v)))

    def test_video_to_vision_without_console(self):
        sent = []
        relay = VideoRelay('127.0.0.1', lambda: None, vision_ip=lambda: '127.0.0.2')
        proto = SimpleNamespace(cam=1, transport=SimpleNamespace(sendto=lambda *a: sent.append(a)))
        data = VIDEO_HEADER.pack(1, 1, 1000)+b'\xff\xd8\x00'
        relay.on_datagram(proto, data, ('127.0.0.1', 1234))
        self.assertEqual(sent, [(data, ('127.0.0.2', 5021))])

    def test_video_fanout_preserves_packet(self):
        sent = []
        relay = VideoRelay('127.0.0.1', lambda: '127.0.0.3', vision_ip=lambda: '127.0.0.2')
        proto = SimpleNamespace(cam=2, transport=SimpleNamespace(sendto=lambda *a: sent.append(a)))
        data = VIDEO_HEADER.pack(2, 5, 1000)+b'\xff\xd8\x00'
        relay.on_datagram(proto, data, ('127.0.0.1', 1234))
        self.assertEqual(sent, [(data, ('127.0.0.2', 5022)), (data, ('127.0.0.3', 5012))])


if __name__ == '__main__':
    unittest.main()
