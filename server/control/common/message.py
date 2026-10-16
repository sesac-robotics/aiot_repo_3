"""
메시지 형식 v1 (SA 설계서 5절)

노드 <-> hub: TCP 위의 줄 단위 JSON (메시지 하나 = JSON 한 줄 + '\\n')
{"v":1, "node":"student3", "seq":1042, "type":"drowsy", "seat":3,
 "ts":1760500000.123, "value":{...}, "conf":0.81}
"""

import json
import time

MSG_VERSION = 1
DEFAULT_PORT = 5000

VALID_NODES = {"hub", "fixedcam", "tracker",
               "student1", "student2", "student3", "student4"}

# 사건 종류와 우선순위 (낮을수록 높음)
INCIDENT_PRIORITY = {
    "fall": 1,
    "hand_raise": 2,
    "leave_seat": 3,
    "drowsy": 4,
    "posture": 5,
}
# node_info: 접속할 때마다 한 번, 카메라 정보와 담당 좌석 (hub 가 cameras / seats 테이블에 저장)
STATE_TYPES = {"presence", "stats", "track_state", "heartbeat", "node_info"}
COMMAND_TYPES = {"track_cmd", "ack"}
VALID_TYPES = set(INCIDENT_PRIORITY) | STATE_TYPES | COMMAND_TYPES

# 사건 종료를 뜻하는 value.state 값
END_STATES = {"end", "return"}

VALID_SEATS = (1, 2, 3, 4, 5)   # 1~4 좌석, 5 = 매트


class MessageError(ValueError):
    pass


def make_message(node: str, seq: int, type_: str, seat=None, value=None,
                 conf: float = 1.0) -> dict:
    return {"v": MSG_VERSION, "node": node, "seq": seq, "type": type_,
            "seat": seat, "ts": round(time.time(), 3),
            "value": value or {}, "conf": conf}


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def parse_message(line: bytes) -> dict:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        raise MessageError(f"JSON 파싱 실패: {e}") from e
    if not isinstance(msg, dict):
        raise MessageError("객체가 아님")

    required = {"v": int, "node": str, "seq": int, "type": str,
                "ts": (int, float), "value": dict, "conf": (int, float)}
    for key, typ in required.items():
        if key not in msg:
            raise MessageError(f"필드 누락: {key}")
        if not isinstance(msg[key], typ):
            raise MessageError(f"타입 오류: {key}")
    if msg["v"] != MSG_VERSION:
        raise MessageError(f"지원하지 않는 버전: {msg['v']}")
    if msg["node"] not in VALID_NODES:
        raise MessageError(f"알 수 없는 노드: {msg['node']}")
    if msg["type"] not in VALID_TYPES:
        raise MessageError(f"알 수 없는 type: {msg['type']}")
    seat = msg.get("seat")
    if seat is not None and seat not in VALID_SEATS:
        raise MessageError(f"잘못된 seat: {seat}")
    return msg
