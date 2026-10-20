"""
노드·메시지 종류·좌석의 유일한 기준 (Control Server 상세 설계 4·5장)

노드 <-> control: TCP 위의 줄 단위 JSON (메시지 하나 = JSON 한 줄 + '\\n')
{"v":1, "node":"vision_service", "seq":1042, "type":"question", "seat":2,
 "ts":1760500000.123, "value":{"state":"start"}, "conf":0.81}

- ts 는 보낸 PC 의 시계. control 은 받은 순간 rx_ts(control 시계)를 붙이고 모든 판단은 rx_ts 로 한다.
- 새 노드 이름, 메시지 종류, 좌석은 반드시 여기에 먼저 추가한다.
"""

import json
import time

MSG_VERSION = 1
DEFAULT_PORT = 5000

# ---- 노드 (4.1) -----------------------------------------------------------
CONTROL_NODE = "control"
VISION_NODE = "vision_service"
CAMERA_NODE = "camera_controller"
CONSOLE_NODE = "instructor_console"
STUDENT_NODES = tuple(f"student_client_{n}" for n in (1, 2, 3, 4))

VALID_NODES = {CONTROL_NODE, VISION_NODE, CAMERA_NODE, CONSOLE_NODE, *STUDENT_NODES}


def student_node(seat: int) -> str:
    return f"student_client_{seat}"


def student_seat(node: str):
    """student_client_N -> N. 학생 노드가 아니면 None."""
    if node in STUDENT_NODES:
        return int(node.rsplit("_", 1)[1])
    return None


# ---- 좌석 (D8: 1~4, 매트 없음) --------------------------------------------
VALID_SEATS = (1, 2, 3, 4)

# ---- 사건 종류와 우선순위 (4.2, 낮을수록 높음) ----------------------------
INCIDENT_PRIORITY = {
    "fall": 1,
    "question": 2,
    "away": 3,
}
INCIDENT_TYPES = set(INCIDENT_PRIORITY)
INCIDENT_STATES = {"start", "end"}           # 사건 메시지의 value.state

# ---- 메시지 종류 (5장) ----------------------------------------------------
# vision_service <-> control
VISION_TYPES = {"track_offset", "explain_text",          # ↑
                "track_target", "explain_request"}       # ↓
# control <-> camera_controller
CAMERA_TYPES = {"pan_tilt",                              # ↓
                "pan_tilt_state"}                        # ↑
# control <-> instructor_console
CONSOLE_TYPES = {"event_update", "queue_state", "device_update",
                 "class_condition", "snapshot",          # ↓
                 "confirm", "resolve"}                   # ↑
# control <-> student_client_N
STUDENT_TYPES = {"fall_alert", "fall_alert_cancel",      # ↓
                 "fall_confirm", "condition_summary"}    # ↑
# 공통
COMMON_TYPES = {"heartbeat", "node_info", "rx_ack"}

VALID_TYPES = INCIDENT_TYPES | VISION_TYPES | CAMERA_TYPES | CONSOLE_TYPES \
    | STUDENT_TYPES | COMMON_TYPES

# messages 표에 기록하지 않는 종류 (너무 많음, D9 / 5.1)
NOT_LOGGED_TYPES = {"heartbeat", "track_offset"}

# 끊긴 동안 NodeClient 가 모아 두었다가 재접속 후 보내는 종류
# (사건 + 사람이 누른 버튼. 상태 메시지는 곧 새 값이 오므로 버린다)
BUFFERED_TYPES = INCIDENT_TYPES | {"confirm", "resolve", "fall_confirm"}

# 하트비트 (5.5)
HEARTBEAT_INTERVAL_S = 2.0
HEARTBEAT_TIMEOUT_S = 6.0


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
    if msg["type"] in INCIDENT_TYPES:
        if seat is None:
            raise MessageError(f"{msg['type']} 에 seat 없음")
        if msg["value"].get("state") not in INCIDENT_STATES:
            raise MessageError(f"{msg['type']} 의 value.state 는 start/end: {msg['value']}")
    return msg
