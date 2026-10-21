"""
common/protocol.py 바이너리 프레임 v3 단위 테스트
확인: 왕복(encode → parse), 헤더 바이트 배치, 스트림 쪼개짐·붙음, 깨진 바이트 건너뛰기, CRC, 값 검사, 옛 JSON 거부

실행 (service/jarvis_control_service 폴더에서):  python3 -m unittest tools.test_protocol -v
"""

import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hub  # noqa: E402,F401  (저장소 최상위를 import 경로에 넣음)

from common.protocol import (HEADER_SIZE, MAGIC, MSG_VERSION, NODE_CODES, TYPE_CODES,   # noqa: E402
                             VALID_NODES, VALID_TYPES,
                             FrameReader, MessageError, encode, make_message, parse_message)


def msg(node="vision_service", type_="question", seat=2, value=None, seq=7, conf=0.9):
    m = make_message(node, seq, type_, seat, {"state": "start"} if value is None else value, conf)
    m["ts"] = 1760500000.123
    return m


class RoundTrip(unittest.TestCase):
    def test_incident(self):
        m = msg()
        self.assertEqual(parse_message(encode(m)), m)

    def test_nested_value(self):
        """event_update·snapshot 처럼 중첩된 값, 한글, None, 실수, 큰 정수"""
        v = {"events": [{"event_id": 17, "kind": "fall", "seat": 3, "detected_at": 1760500000.5,
                         "confirmed_at": None, "alert_stage": "neighbor", "overdue": False,
                         "explain": "3번 좌석 학생이 쓰러졌습니다"}],
             "queue": {"active": 17, "waiting": [18, 15]},
             "devices": [{"node": "student_client_1", "online": True, "note": ""}],
             "big": 2 ** 40, "neg": -5, "empty": {}, "tuple": (1, 2)}
        out = parse_message(encode(msg(node="control", type_="snapshot", seat=None, value=v)))
        v["tuple"] = [1, 2]                         # JSON 과 같이 tuple 은 list 로
        self.assertEqual(out["value"], v)
        self.assertIsNone(out["seat"])

    def test_int_keys_become_str(self):
        out = parse_message(encode(msg(node="control", type_="queue_state", seat=None, value={1: "a"})))
        self.assertEqual(out["value"], {"1": "a"})

    def test_all_types_have_codes(self):
        self.assertEqual(set(TYPE_CODES), VALID_TYPES)
        self.assertEqual(set(NODE_CODES), VALID_NODES)
        self.assertEqual(NODE_CODES["student_client_3"], 13)


class Layout(unittest.TestCase):
    def test_header_bytes(self):
        b = encode(msg(node="student_client_1", type_="condition_summary", seat=1,
                       value={"focus_low": True, "posture_bad": False}, seq=0x01020304))
        self.assertEqual(b[:2], MAGIC)
        self.assertEqual(b[2], MSG_VERSION)
        self.assertEqual(b[3], 11)                  # student_client_1
        self.assertEqual(b[4], 0x53)                # condition_summary
        self.assertEqual(b[5], 1)                   # seat
        self.assertEqual(b[6:10], bytes([1, 2, 3, 4]))
        length = struct.unpack(">I", b[22:26])[0]
        self.assertEqual(len(b), HEADER_SIZE + length + 2)


class Stream(unittest.TestCase):
    def test_split_and_joined(self):
        frames = [encode(msg(seq=i)) for i in range(5)]
        data = b"".join(frames)
        r = FrameReader()
        got = []
        for i in range(0, len(data), 3):            # 3바이트씩 쪼개 넣기
            got += r.feed(data[i:i + 3])
        self.assertEqual([parse_message(f)["seq"] for f in got], list(range(5)))
        self.assertEqual(r.errors, [])

    def test_garbage_skipped(self):
        r = FrameReader()
        good = encode(msg(seq=1))
        got = r.feed(b"hello\n\xAA\x55\x03garbage" + good + b"\x00\x01")
        self.assertEqual([parse_message(f)["seq"] for f in got], [1])
        self.assertTrue(r.errors)

    def test_crc_corruption(self):
        bad = bytearray(encode(msg(seq=1)))
        bad[HEADER_SIZE] ^= 0xFF                    # 값 1바이트 손상
        r = FrameReader()
        got = r.feed(bytes(bad) + encode(msg(seq=2)))
        self.assertEqual([parse_message(f)["seq"] for f in got], [2])
        self.assertTrue(any("CRC" in e for e in r.errors))

    def test_legacy_json_rejected(self):
        line = (json.dumps({"v": 1, "node": "vision_service", "seq": 1, "type": "heartbeat",
                            "ts": 1.0, "value": {}, "conf": 1.0}) + "\n").encode()
        r = FrameReader()
        self.assertEqual(r.feed(line), [])
        self.assertTrue(r.errors)
        with self.assertRaises(MessageError):
            parse_message(line)

    def test_legacy_v2_packet_does_not_block(self):
        """student_client 시험 패킷(v2, 14바이트)도 AA 55 로 시작한다. 잘못 들어와도 뒤의 정상 프레임이 막히지 않아야 한다"""
        old_v2 = bytes.fromhex("AA 55 02 10 01 01 00 00 01 6A D5 DA 80 F6")
        r = FrameReader()
        got = r.feed(old_v2 + encode(msg(seq=5)))
        self.assertEqual([parse_message(f)["seq"] for f in got], [5])
        self.assertTrue(any("헤더 오류" in e for e in r.errors))


class Validation(unittest.TestCase):
    def test_bad_seat(self):
        with self.assertRaises(MessageError):
            parse_message(encode(msg(seat=9)))

    def test_incident_needs_state(self):
        with self.assertRaises(MessageError):
            parse_message(encode(msg(value={"state": "maybe"})))

    def test_condition_summary_fields(self):
        with self.assertRaises(MessageError):
            parse_message(encode(msg(node="student_client_1", type_="condition_summary", seat=1,
                                     value={"drowsy": True})))
        ok = parse_message(encode(msg(node="student_client_1", type_="condition_summary", seat=1,
                                      value={"focus_low": True, "posture_bad": False})))
        self.assertEqual(ok["value"], {"focus_low": True, "posture_bad": False})

    def test_encode_rejects_unknown(self):
        with self.assertRaises(MessageError):
            encode(msg(node="nobody"))
        with self.assertRaises(MessageError):
            encode(msg(value={"x": object()}))
        with self.assertRaises(MessageError):
            encode(msg(seat=300))


if __name__ == "__main__":
    unittest.main()
