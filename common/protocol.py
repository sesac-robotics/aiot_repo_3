"""
노드·메시지 종류·좌석의 유일한 기준 (Control Server 상세 설계 4·5장)

노드 <-> control: TCP 위의 바이너리 프레임 (메시지 하나 = 프레임 하나). 형식 v3
  (v1 = 줄 단위 JSON, 더 이상 쓰지 않음. 2번은 폐기된 student_client 시험 패킷이 썼으므로 건너뜀 — control 은 받지 않는다)

프레임 (빅엔디언)
  바이트      필드        형식      내용
  0-1        magic       2s        시작 표시 0xAA 0x55
  2          version     uint8     MSG_VERSION (3)
  3          node        uint8     보낸 노드 코드 (NODE_CODES)
  4          type        uint8     메시지 종류 코드 (TYPE_CODES)
  5          seat        uint8     좌석 1~4, 0 = 없음
  6-9        seq         uint32    보낸 노드의 일련번호
  10-17      ts          float64   보낸 PC 의 시각 (초)
  18-21      conf        float32   신뢰도 0~1
  22-25      length      uint32    값(payload) 바이트 수 (최대 MAX_PAYLOAD)
  26-…       payload     TLV       value dict (아래 값 인코딩)
  끝 2바이트  crc         uint16    CRC-16/CCITT (version ~ payload 끝)

값 인코딩 (TLV: 태그 1바이트 + 내용)
  0x00 None | 0x01 False | 0x02 True
  0x03 int   int64
  0x04 float float64
  0x05 str   uint32 길이 + UTF-8
  0x06 list  uint32 개수 + 값…
  0x07 dict  uint32 개수 + (키: uint16 길이 + UTF-8, 값)…

TCP 는 경계가 없는 스트림이므로 받는 쪽은 FrameReader 로 시작 표시를 찾아 프레임 단위로 자른다.
깨진 프레임(CRC 불일치 등)은 1바이트씩 밀며 다음 시작 표시를 찾는다.

encode()/parse_message() 가 주고받는 dict 는 v1 과 같다:
{"v":3, "node":"vision_service", "seq":1042, "type":"question", "seat":2,
 "ts":1760500000.123, "value":{"state":"start"}, "conf":0.81}
그래서 노드 코드·event_manager 는 형식이 바뀌어도 고칠 것이 없다.

- ts 는 보낸 PC 의 시계. control 은 받은 순간 rx_ts(control 시계)를 붙이고 모든 판단은 rx_ts 로 한다.
- 새 노드 이름, 메시지 종류, 좌석은 반드시 여기에 먼저 추가한다 (코드 표도 같이).
"""

import binascii
import struct
import time

MSG_VERSION = 3
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
                "seat_obs", "track_obs",                 # ↑ [팬틸트] track_offset 대체 {gen, frame_id, captured_at, status, cx, cy}
                "track_target", "explain_request"}       # ↓
# control <-> camera_controller
CAMERA_TYPES = {"pan_tilt",                              # ↓
                "pan_tilt_state"}                        # ↑
# control <-> instructor_console
CONSOLE_TYPES = {"event_update", "queue_state", "device_update",
                 "class_condition", "snapshot",          # ↓
                 "preset_saved",                         # ↓ [팬틸트] {seat, pan, tilt}
                 "confirm", "resolve",                   # ↑
                 "pan_tilt_jog", "preset_save"}          # ↑ [팬틸트] 프리셋 등록 화살표 {dpan, dtilt} / 등록 버튼 {seat}
# control <-> student_client_N
STUDENT_TYPES = {"fall_alert", "fall_alert_cancel",      # ↓
                 "fall_confirm", "condition_summary"}    # ↑
# 공통
COMMON_TYPES = {"heartbeat", "node_info", "rx_ack"}

VALID_TYPES = INCIDENT_TYPES | VISION_TYPES | CAMERA_TYPES | CONSOLE_TYPES \
    | STUDENT_TYPES | COMMON_TYPES

# messages 표에 기록하지 않는 종류 (너무 많음, D9 / 5.1)
NOT_LOGGED_TYPES = {"heartbeat", "track_offset", "track_obs", "seat_obs"}

# 끊긴 동안 NodeClient 가 모아 두었다가 재접속 후 보내는 종류
# (사건 + 사람이 누른 버튼. 상태 메시지는 곧 새 값이 오므로 버린다)
BUFFERED_TYPES = INCIDENT_TYPES | {"confirm", "resolve", "fall_confirm", "preset_save"}

# 하트비트 (5.5)
HEARTBEAT_INTERVAL_S = 2.0
HEARTBEAT_TIMEOUT_S = 6.0

# ---- 바이너리 코드 표 (프레임의 node / type 바이트) -------------------------
# 노드 코드는 DB device.node 번호와 맞춘다 (0 control · 1 vision · 3 console · 11~14 학생)
NODE_CODES = {
    CONTROL_NODE: 0,
    VISION_NODE: 1,
    CAMERA_NODE: 2,
    CONSOLE_NODE: 3,
    **{student_node(s): 10 + s for s in VALID_SEATS},
}
TYPE_CODES = {
    # 공통 0x0_
    "heartbeat": 0x01, "node_info": 0x02, "rx_ack": 0x03,
    # 사건 0x1_
    "fall": 0x10, "question": 0x11, "away": 0x12,
    # vision 0x2_
    "track_offset": 0x20, "explain_text": 0x21, "track_target": 0x22, "explain_request": 0x23,
    "track_obs": 0x24, "seat_obs": 0x25,
    # camera 0x3_
    "pan_tilt": 0x30, "pan_tilt_state": 0x31,
    # console 0x4_
    "event_update": 0x40, "queue_state": 0x41, "device_update": 0x42, "class_condition": 0x43,
    "snapshot": 0x44, "confirm": 0x45, "resolve": 0x46,
    "pan_tilt_jog": 0x47, "preset_save": 0x48, "preset_saved": 0x49,
    # student 0x5_
    "fall_alert": 0x50, "fall_alert_cancel": 0x51, "fall_confirm": 0x52, "condition_summary": 0x53,
}
CODE_NODES = {v: k for k, v in NODE_CODES.items()}
CODE_TYPES = {v: k for k, v in TYPE_CODES.items()}
assert set(NODE_CODES) == VALID_NODES and len(CODE_NODES) == len(NODE_CODES), "NODE_CODES 를 노드 목록과 맞춘다"
assert set(TYPE_CODES) == VALID_TYPES and len(CODE_TYPES) == len(TYPE_CODES), "TYPE_CODES 를 메시지 종류와 맞춘다"

# ---- 카메라 영상 (UDP. 위 TCP 프레임과 별개) -----------------------------------
# camera_streamer -> control(hub) -> instructor_console. hub 는 저장하지 않고 그대로 넘긴다.
# 데이터그램 하나 = 헤더 16바이트 + JPEG 한 장 (빅엔디언)
#   0      cam_id       uint8     1 고정 카메라, 2 팬틸트 카메라
#   1-3    (예약)        3바이트   0
#   4-7    frame_id     uint32    카메라별 일련번호 (순서가 뒤집힌 옛 프레임을 버리는 데 씀)
#   8-15   captured_at  float64   촬영 시각 (보낸 PC 시계)
VIDEO_HEADER = struct.Struct(">B3xId")
VIDEO_CAMS = (1, 2)
VIDEO_PORTS = {1: 6001, 2: 6002}              # streamer -> hub (hub 가 받는 포트)
CONSOLE_VIDEO_PORTS = {1: 5011, 2: 5012}      # hub -> instructor_console (Console 이 받는 포트)
VISION_VIDEO_PORTS = {1: 5021, 2: 5022}       # hub -> vision_service
VIDEO_MAX_DATAGRAM = 65507                    # UDP(IPv4) 데이터그램 최대 크기
VIDEO_MAX_JPEG = VIDEO_MAX_DATAGRAM - VIDEO_HEADER.size

MAGIC = b"\xAA\x55"
_HEADER = struct.Struct(">2sBBBBIdfI")         # magic, version, node, type, seat, seq, ts, conf, length
HEADER_SIZE = _HEADER.size                     # 26
CRC_SIZE = 2
MIN_FRAME = HEADER_SIZE + CRC_SIZE
MAX_PAYLOAD = 1 << 20                          # 1 MiB. 이보다 크면 깨진 프레임으로 본다
MAX_DEPTH = 32                                 # 값 중첩 한도


class MessageError(ValueError):
    pass


def make_message(node: str, seq: int, type_: str, seat=None, value=None,
                 conf: float = 1.0) -> dict:
    return {"v": MSG_VERSION, "node": node, "seq": seq, "type": type_,
            "seat": seat, "ts": round(time.time(), 3),
            "value": value or {}, "conf": conf}


# ---- 값 인코딩 (TLV) --------------------------------------------------------
T_NONE, T_FALSE, T_TRUE, T_INT, T_FLOAT, T_STR, T_LIST, T_DICT = range(8)
_I64, _F64, _U32, _U16 = struct.Struct(">q"), struct.Struct(">d"), struct.Struct(">I"), struct.Struct(">H")


def _enc_value(x, out: bytearray, depth: int = 0):
    if depth > MAX_DEPTH:
        raise MessageError("값 중첩이 너무 깊음")
    if x is None:
        out.append(T_NONE)
    elif x is True:
        out.append(T_TRUE)
    elif x is False:
        out.append(T_FALSE)
    elif isinstance(x, int):
        if not -(1 << 63) <= x < (1 << 63):
            raise MessageError(f"int64 범위 밖: {x}")
        out.append(T_INT)
        out += _I64.pack(x)
    elif isinstance(x, float):
        out.append(T_FLOAT)
        out += _F64.pack(x)
    elif isinstance(x, str):
        b = x.encode("utf-8")
        out.append(T_STR)
        out += _U32.pack(len(b)) + b
    elif isinstance(x, (list, tuple)):
        out.append(T_LIST)
        out += _U32.pack(len(x))
        for item in x:
            _enc_value(item, out, depth + 1)
    elif isinstance(x, dict):
        out.append(T_DICT)
        out += _U32.pack(len(x))
        for k, v in x.items():
            kb = (k if isinstance(k, str) else str(k)).encode("utf-8")   # JSON 처럼 키는 문자열
            if len(kb) > 0xFFFF:
                raise MessageError("dict 키가 너무 김")
            out += _U16.pack(len(kb)) + kb
            _enc_value(v, out, depth + 1)
    else:
        raise MessageError(f"보낼 수 없는 값 형식: {type(x).__name__}")


def _dec_value(buf: bytes, i: int, depth: int = 0):
    """buf[i:] 에서 값 하나 → (값, 다음 위치)"""
    if depth > MAX_DEPTH:
        raise MessageError("값 중첩이 너무 깊음")
    tag = buf[i]
    i += 1
    if tag == T_NONE:
        return None, i
    if tag == T_FALSE:
        return False, i
    if tag == T_TRUE:
        return True, i
    if tag == T_INT:
        return _I64.unpack_from(buf, i)[0], i + 8
    if tag == T_FLOAT:
        return _F64.unpack_from(buf, i)[0], i + 8
    if tag == T_STR:
        n = _U32.unpack_from(buf, i)[0]
        i += 4
        if i + n > len(buf):
            raise MessageError("문자열 길이가 값 영역을 넘음")
        return buf[i:i + n].decode("utf-8"), i + n
    if tag == T_LIST:
        n = _U32.unpack_from(buf, i)[0]
        i += 4
        if n > len(buf) - i:                       # 원소마다 최소 1바이트
            raise MessageError("list 개수가 값 영역을 넘음")
        out = []
        for _ in range(n):
            v, i = _dec_value(buf, i, depth + 1)
            out.append(v)
        return out, i
    if tag == T_DICT:
        n = _U32.unpack_from(buf, i)[0]
        i += 4
        if n > len(buf) - i:
            raise MessageError("dict 개수가 값 영역을 넘음")
        out = {}
        for _ in range(n):
            kn = _U16.unpack_from(buf, i)[0]
            i += 2
            if i + kn > len(buf):
                raise MessageError("dict 키 길이가 값 영역을 넘음")
            k = buf[i:i + kn].decode("utf-8")
            i += kn
            out[k], i = _dec_value(buf, i, depth + 1)
        return out, i
    raise MessageError(f"알 수 없는 값 태그: 0x{tag:02x}")


# ---- 프레임 ---------------------------------------------------------------
def _crc(body: bytes) -> int:
    return binascii.crc_hqx(body, 0xFFFF)          # CRC-16/CCITT-FALSE


def encode(msg: dict) -> bytes:
    """make_message() 의 dict → 바이너리 프레임"""
    try:
        node, type_ = NODE_CODES[msg["node"]], TYPE_CODES[msg["type"]]
    except KeyError as e:
        raise MessageError(f"코드 표에 없는 노드/종류: {e}") from None
    seat = msg.get("seat")
    if seat is None:
        seat = 0
    elif not (isinstance(seat, int) and not isinstance(seat, bool) and 1 <= seat <= 255):
        raise MessageError(f"seat 은 1~255 정수 또는 None: {seat!r}")
    payload = bytearray()
    _enc_value(msg.get("value") or {}, payload)
    if len(payload) > MAX_PAYLOAD:
        raise MessageError(f"값이 너무 큼: {len(payload)} 바이트")
    header = _HEADER.pack(MAGIC, MSG_VERSION, node, type_, seat, msg["seq"] & 0xFFFFFFFF,
                          float(msg["ts"]), float(msg.get("conf", 1.0)), len(payload))
    body = header[2:] + payload
    return MAGIC + body + _U16.pack(_crc(body))


def _decode_frame(frame: bytes) -> dict:
    if len(frame) < MIN_FRAME:
        raise MessageError(f"프레임이 너무 짧음: {len(frame)} 바이트")
    magic, ver, node, type_, seat, seq, ts, conf, length = _HEADER.unpack_from(frame)
    if magic != MAGIC:
        raise MessageError("시작 표시 불일치")
    if len(frame) != HEADER_SIZE + length + CRC_SIZE:
        raise MessageError(f"길이 불일치: 프레임 {len(frame)}, 값 {length}")
    if _U16.unpack_from(frame, len(frame) - CRC_SIZE)[0] != _crc(frame[2:-CRC_SIZE]):
        raise MessageError("CRC 불일치")
    if ver != MSG_VERSION:
        raise MessageError(f"지원하지 않는 버전: {ver}")
    if node not in CODE_NODES:
        raise MessageError(f"알 수 없는 노드 코드: {node}")
    if type_ not in CODE_TYPES:
        raise MessageError(f"알 수 없는 type 코드: 0x{type_:02x}")
    payload = frame[HEADER_SIZE:-CRC_SIZE]
    try:
        value, end = _dec_value(payload, 0) if payload else ({}, 0)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise MessageError(f"값 해석 실패: {e}") from e
    if end != len(payload):
        raise MessageError("값 뒤에 남는 바이트가 있음")
    if not isinstance(value, dict):
        raise MessageError("value 가 dict 가 아님")
    return {"v": ver, "node": CODE_NODES[node], "seq": seq, "type": CODE_TYPES[type_],
            "seat": seat or None, "ts": round(ts, 3), "value": value, "conf": round(conf, 4)}


def parse_message(frame: bytes) -> dict:
    """바이너리 프레임 하나 → 검사를 마친 메시지 dict. 문제가 있으면 MessageError."""
    msg = _decode_frame(bytes(frame))
    seat = msg["seat"]
    if seat is not None and seat not in VALID_SEATS:
        raise MessageError(f"잘못된 seat: {seat}")
    if msg["type"] in INCIDENT_TYPES:
        if seat is None:
            raise MessageError(f"{msg['type']} 에 seat 없음")
        if msg["value"].get("state") not in INCIDENT_STATES:
            raise MessageError(f"{msg['type']} 의 value.state 는 start/end: {msg['value']}")
    _check_value(msg["type"], msg["value"])
    return msg


class FrameReader:
    """TCP 스트림 조각을 넣으면 완성된 프레임(bytes)을 꺼내 준다.
    시작 표시가 없거나 CRC 가 틀린 바이트는 건너뛰고 errors 에 이유를 남긴다 (한 연결이 깨진 데이터로 막히지 않게).
    꺼낸 프레임은 parse_message() 로 해석한다."""

    def __init__(self):
        self._buf = bytearray()
        self.errors: list[str] = []

    def feed(self, chunk: bytes) -> list:
        self._buf.extend(chunk)
        out = []
        while True:
            i = self._buf.find(MAGIC)
            if i < 0:                               # 시작 표시 없음: 마지막 1바이트만 남김 (0xAA 가 잘렸을 수 있음)
                if len(self._buf) > 1:
                    self.errors.append(f"시작 표시 없는 {len(self._buf) - 1}바이트 버림: "
                                       f"{bytes(self._buf[:16]).hex(' ')}…")
                    del self._buf[:-1]
                return out
            if i > 0:
                self.errors.append(f"시작 표시 앞 {i}바이트 버림")
                del self._buf[:i]
            if len(self._buf) < HEADER_SIZE:
                return out
            # 길이를 믿기 전에 헤더부터 확인한다. 우연히 AA 55 가 나온 쓰레기(예: 옛 v2 패킷)의
            # 엉뚱한 길이를 믿으면 오지 않을 바이트를 기다리느라 뒤의 정상 프레임까지 막힌다.
            ver, node, type_ = self._buf[2], self._buf[3], self._buf[4]
            length = _U32.unpack_from(self._buf, HEADER_SIZE - 4)[0]
            bad = (f"버전 {ver}" if ver != MSG_VERSION else
                   f"노드 코드 {node}" if node not in CODE_NODES else
                   f"type 코드 0x{type_:02x}" if type_ not in CODE_TYPES else
                   f"값 길이 {length}" if length > MAX_PAYLOAD else None)
            if bad:
                self.errors.append(f"헤더 오류({bad}) - 1바이트 밀어 다시 찾음: {bytes(self._buf[:16]).hex(' ')}…")
                del self._buf[:1]
                continue
            size = HEADER_SIZE + length + CRC_SIZE
            if len(self._buf) < size:
                return out
            frame = bytes(self._buf[:size])
            if _U16.unpack_from(frame, size - CRC_SIZE)[0] != _crc(frame[2:-CRC_SIZE]):
                self.errors.append(f"CRC 불일치 - 1바이트 밀어 다시 찾음: {frame[:16].hex(' ')}…")
                del self._buf[:1]
                continue
            del self._buf[:size]
            out.append(frame)


# ---- value 값 검사 (노드 -> control 메시지) --------------------------------
# 잘못된 값이 event_manager·DB 까지 가면 이벤트가 깨지므로 받는 순간 버린다.
def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_scalar(x) -> bool:
    return x is None or isinstance(x, (str, int, float))


def _check_value(type_: str, v: dict):
    def need(cond, what):
        if not cond:
            raise MessageError(f"{type_} 의 value 오류 ({what}): {v}")

    if type_ in ("confirm", "resolve"):
        need(_is_int(v.get("event_id")), "event_id 는 정수")
    elif type_ == "explain_text":
        need(_is_int(v.get("event_id")), "event_id 는 정수")
        need(isinstance(v.get("text", ""), str), "text 는 문자열")
        need(v.get("mode") is None or isinstance(v["mode"], str), "mode 는 문자열")
    elif type_ == "fall_confirm":
        need(v.get("target_seat") in VALID_SEATS and _is_int(v.get("target_seat")),
             "target_seat 은 좌석 번호")
    elif type_ == "track_offset":
        if v.get("found"):
            need(_is_num(v.get("dx", 0.0)) and _is_num(v.get("dy", 0.0)), "dx, dy 는 숫자")
    elif type_ == "seat_obs":
        import math
        need(isinstance(v.get("session"), str) and 0 < len(v["session"]) <= 128, "session")
        need(_is_int(v.get("frame_id")) and v["frame_id"] >= 0, "frame_id")
        for key in ("captured_at", "processed_at"):
            need(_is_num(v.get(key)) and math.isfinite(v[key]), key)
        rows = v.get("seats")
        need(isinstance(rows, list) and 1 <= len(rows) <= 4, "seats")
        seen = set()
        for row in rows:
            need(isinstance(row, dict), "seat row")
            seat = row.get("seat")
            need(_is_int(seat) and seat in VALID_SEATS and seat not in seen, "unique seat")
            seen.add(seat)
            for key in ("present", "hand_up"):
                need(key in row and (row[key] is None or type(row[key]) is bool), key)
    elif type_ == "track_obs":
        need(_is_int(v.get("gen")), "gen 은 정수")
        if v.get("status") == "TRACKED":
            need(_is_num(v.get("cx")) and _is_num(v.get("cy")), "cx, cy 는 숫자")
    elif type_ == "pan_tilt_jog":
        need(_is_num(v.get("dpan")) and _is_num(v.get("dtilt")), "dpan, dtilt 는 숫자")
    elif type_ == "preset_save":
        need(v.get("seat") in VALID_SEATS and _is_int(v.get("seat")), "seat 은 좌석 번호")
    elif type_ == "condition_summary":
        # 필드 이름이 틀리면 반 집계에서 조용히 '정상'으로 세어지므로 받는 순간 버린다
        need(all(isinstance(v.get(k), bool) for k in ("focus_low", "posture_bad")),
             "focus_low, posture_bad 는 true/false")
    elif type_ == "heartbeat":
        need(all(v.get(k) is None or _is_num(v[k]) for k in ("fps", "cpu", "cam1_fps", "cam2_fps")),
             "fps, cpu, cam1_fps, cam2_fps 는 숫자")
    elif type_ == "node_info":
        pc = v.get("pc", {})
        need(isinstance(pc, dict) and all(_is_scalar(x) for x in pc.values()),
             "pc 는 문자열·숫자 값만")
