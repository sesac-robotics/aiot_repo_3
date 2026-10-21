"""[폐기] control 통신은 common/protocol.py 바이너리 프레임 v3 를 쓴다 (seat_link/control_link.py). 이 파일은 기록용.

학생 상태 바이너리 패킷 v2 (시험용): 판정 결과 bool 4개 ↔ 14바이트 고정 길이 패킷 (형식이 바뀌면 이 파일만 고친다)

바이트 배치 (빅엔디언)
  0-1   시작 표시    0xAA 0x55
  2     버전        0x02
  3     메시지 종류   0x10 = 상태 요약
  4     좌석        1~255
  5     drowsy      졸음            0/1
  6     yawn        하품            0/1
  7     not_forward 전방 주시 미흡    0/1
  8     forward_lean 앞으로 숙임      0/1
  9-12  시각        보낸 시각 (초, uint32)
  13    체크섬       2~12번 바이트 XOR
TCP는 경계가 없는 스트림이므로, 받는 쪽은 PacketReader로 시작 표시를 찾아 14바이트씩 잘라 읽는다.
"""
import struct
import time

MAGIC = b"\xAA\x55"
VERSION = 2
MSG_STATUS = 0x10
FIELDS = ("drowsy", "yawn", "not_forward", "forward_lean")
_FMT = struct.Struct(">2sBBB" + "B" * len(FIELDS) + "IB")
SIZE = _FMT.size                                    # 14


class PacketError(ValueError):
    pass


def _checksum(body: bytes) -> int:
    x = 0
    for b in body:
        x ^= b
    return x


def encode(seat: int, flags: dict, ts=None) -> bytes:
    """flags: {"drowsy": True, ...} (없는 키는 0) → 14바이트"""
    if not 0 <= seat <= 255:
        raise PacketError(f"좌석 범위 밖: {seat}")
    ts = int(time.time() if ts is None else ts) & 0xFFFFFFFF
    values = [1 if flags.get(k) else 0 for k in FIELDS]
    raw = _FMT.pack(MAGIC, VERSION, MSG_STATUS, seat, *values, ts, 0)
    return raw[:-1] + bytes([_checksum(raw[2:-1])])


def decode(data: bytes) -> dict:
    """14바이트 → {"version", "type", "seat", "ts", "flags": {"drowsy": bool, ...}}"""
    if len(data) != SIZE:
        raise PacketError(f"길이 {len(data)} (기대 {SIZE})")
    magic, ver, typ, seat, *rest = _FMT.unpack(data)
    values, ts, chk = rest[:len(FIELDS)], rest[-2], rest[-1]
    if magic != MAGIC:
        raise PacketError("시작 표시 불일치")
    if chk != _checksum(data[2:-1]):
        raise PacketError("체크섬 불일치")
    if ver != VERSION:
        raise PacketError(f"지원하지 않는 버전: {ver}")
    if any(v not in (0, 1) for v in values):
        raise PacketError(f"bool 값이 0/1이 아님: {values}")
    return {"version": ver, "type": typ, "seat": seat, "ts": ts,
            "flags": {k: bool(v) for k, v in zip(FIELDS, values)}}


class PacketReader:
    """TCP 스트림 조각을 넣으면 완성된 패킷을 꺼내 준다. 깨진 패킷은 errors에 남기고 건너뛴다"""

    def __init__(self):
        self._buf = bytearray()
        self.errors = []

    def feed(self, chunk: bytes) -> list:
        self._buf.extend(chunk)
        out = []
        while True:
            i = self._buf.find(MAGIC)
            if i < 0:                               # 시작 표시 없음: 마지막 1바이트만 남김 (0xAA가 잘렸을 수 있음)
                del self._buf[:-1]
                return out
            del self._buf[:i]
            if len(self._buf) < SIZE:
                return out
            frame = bytes(self._buf[:SIZE])
            try:
                out.append(decode(frame))
                del self._buf[:SIZE]
            except PacketError as e:
                self.errors.append(f"{e}: {frame.hex(' ')}")
                del self._buf[:1]                   # 1바이트 밀어 다음 시작 표시를 찾음