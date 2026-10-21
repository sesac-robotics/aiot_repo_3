"""Uno 시리얼 통신 (IF-04, ASCII). poll()을 20ms마다 호출한다.

port=None이면 Uno 대신 같은 프로토콜의 모의 장치(SimDevice)를 쓴다.
"""
import logging
from dataclasses import dataclass
from collections import deque

from .preset_store import FW_LIMITS

log = logging.getLogger(__name__)
(PAN_MIN, PAN_MAX), (TILT_MIN, TILT_MAX) = FW_LIMITS['pan'], FW_LIMITS['tilt']


@dataclass
class Pending:
    line: str
    sent_at: float
    retries: int = 0


class SimDevice:
    """정수 1° 갱신 모의 장치. 기계 부하·실제 영상·USB 물리 장애는 모의하지 않는다."""
    def __init__(self):
        self.out = deque(['R'])
        self.cur = [81, 90]
        self.target = self.cur[:]
        self.limits = [PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX]
        self.home = self.cur[:]
        self.configured = self.started = False
        self.active_seq = None
        self.done_required = False
        self.mode = 'S'
        self.last_step = self.last_rx = self.now = 0.0
        self.timed_out = False
        self.led = 'O'
        self.last_line = None
        self.cached = []

    def write(self, raw):
        line = raw.decode('ascii').strip()
        parts = line.split(',')
        op = parts[0]
        if op == 'B':
            self.last_rx = self.now
            return
        seq = int(parts[1])
        if line == self.last_line:
            self.last_rx = self.now
            self.out.extend(self.cached)
            return
        replies = []
        if op == 'C':
            v = list(map(int, parts[2:]))
            p0, p1, t0, t1, hp, ht = v
            if not (PAN_MIN <= p0 <= hp <= p1 <= PAN_MAX
                    and TILT_MIN <= t0 <= ht <= t1 <= TILT_MAX):
                replies = [f'E,{seq},2']
            elif self.started:
                replies = [f'E,{seq},3']
            else:
                self.limits, self.home = v[:4], v[4:]
                self.configured = True
                replies = [f'K,{seq}']
        elif not self.configured or (op in ('S', 'T', 'X') and not self.started):
            replies = [f'E,{seq},3']
        elif op == 'L':
            self.led = parts[2]
            replies = [f'K,{seq}']
        else:
            target = self.home[:] if op == 'H' else (
                self.cur[:] if op == 'X' else list(map(int, parts[2:])))
            if not (self.limits[0] <= target[0] <= self.limits[1]
                    and self.limits[2] <= target[1] <= self.limits[3]):
                replies = [f'E,{seq},2']
            else:
                if not self.started:
                    self.cur = self.home[:]
                    self.started = True
                self.target = target
                if self.timed_out:
                    self.led = 'O'
                self.timed_out = False
                # 반복 T 수신 시 타이머를 매번 초기화하지 않아 속도 저하를 방지한다.
                if op != self.mode:
                    self.last_step = self.now
                self.mode = op
                self.active_seq = seq
                self.done_required = op in ('S', 'H')
                replies = [f'K,{seq},{self.cur[0]},{self.cur[1]}']
                if self.done_required and self.cur == self.target:
                    replies.append(f'D,{seq},{self.cur[0]},{self.cur[1]}')
                    self.done_required = False
        if not replies[0].startswith('E'):
            self.last_rx = self.now
            self.last_line, self.cached = line, replies[:]
        self.out.extend(replies)

    def tick(self, now):
        self.now = now
        if self.configured and now - self.last_rx >= 3 and not self.timed_out:
            self.target = self.cur[:]
            self.done_required = False
            self.timed_out = True
            self.out.append('E,0,9')
        interval = .04 if self.mode == 'T' else .02
        # 부동소수점 오차(0.02 → 0.01999…)로 한 주기를 건너뛰지 않도록 0.1ms 여유를 둔다.
        if self.started and now - self.last_step >= interval - 1e-4:
            self.last_step = now
            for i in range(2):
                self.cur[i] += (self.target[i] > self.cur[i]) - (self.target[i] < self.cur[i])
            if self.done_required and self.cur == self.target:
                line = f'D,{self.active_seq},{self.cur[0]},{self.cur[1]}'
                self.out.append(line)
                if self.last_line and self.last_line.split(',')[1] == str(self.active_seq):
                    self.cached.append(line)
                self.done_required = False

    def close(self):
        pass


class SerialLink:
    def __init__(self, port=None):
        self.sim = port is None
        if self.sim:
            self.io = SimDevice()
        else:
            import serial
            self.io = serial.Serial(port, 9600, timeout=0, write_timeout=.2)
        self.buffer = bytearray()
        self.seq = 0
        self.pending = {}
        self.last_move = None
        self.last_heartbeat = 0
        self.ready = False
        self.error = None
        self.unanswered_since = None

    def send(self, op, *args, now):
        if op == 'L':
            for old_seq, pending in list(self.pending.items()):
                if pending.line.startswith('L,'):
                    self.pending.pop(old_seq)
        if op in ('S', 'T', 'H', 'X'):
            # 오래된 이동의 재전송을 취소한다. 도착한 응답은 제어부가 seq로 구분한다.
            if self.last_move is not None:
                self.pending.pop(self.last_move, None)
        for _ in range(999):
            self.seq = self.seq % 999 + 1
            if self.seq not in self.pending:
                break
        else:
            raise RuntimeError('seq exhausted')
        if self.unanswered_since is None:
            self.unanswered_since = now
        line = ','.join(map(str, (op, self.seq, *args)))
        self.io.write((line + '\n').encode('ascii'))
        log.debug('TX %s', line)
        self.pending[self.seq] = Pending(line, now)
        if op in ('S', 'T', 'H', 'X'):
            self.last_move = self.seq
        return self.seq

    def _lines(self):
        if self.sim:
            lines = list(self.io.out)
            self.io.out.clear()
            return lines
        self.buffer.extend(self.io.read(self.io.in_waiting))
        if len(self.buffer) > 4096:
            raise RuntimeError('수신 버퍼 초과')
        lines = []
        while b'\n' in self.buffer:
            line, _, tail = self.buffer.partition(b'\n')
            self.buffer = bytearray(tail)
            lines.append(line.decode('ascii').strip())
        return lines

    def poll(self, now):
        if self.sim:
            self.io.tick(now)
        if self.ready and now - self.last_heartbeat >= 1:
            self.io.write(b'B\n')
            self.last_heartbeat = now
        messages = self._lines()
        for line in messages:
            log.debug('RX %s', line)
            p = line.split(',')
            if p == ['R']:
                self.pending.clear()
                self.ready = True
                self.error = None
                self.unanswered_since = None
            elif p[0] == 'K':
                self.pending.pop(int(p[1]), None)
                self.unanswered_since = now if self.pending else None
            elif p[0] == 'E':
                self.error = line
        for seq, pending in list(self.pending.items()):
            if now - pending.sent_at >= .5:
                if pending.retries:
                    self.error = f'ACK timeout seq={seq}'
                    self.pending.pop(seq)
                else:
                    self.io.write((pending.line + '\n').encode('ascii'))
                    pending.sent_at = now
                    pending.retries += 1
        # 최신 T로 계속 교체되어도 전체 응답 단절은 검출한다.
        if (self.pending and self.unanswered_since is not None
                and now - self.unanswered_since >= 1):
            self.error = '연속 명령 응답 단절(1초)'
        return messages

    def close(self):
        self.io.close()


ARDUINO_VIDS = (0x2341, 0x2A03, 0x1A86, 0x0403)     # Arduino, 호환 보드(CH340, FTDI)


def find_uno_port():
    """연결된 Uno 포트를 찾는다. (포트 또는 None, 연결된 포트 목록 [(장치, 설명)])

    Arduino로 보이는 포트가 정확히 하나일 때만 그 포트를 돌려준다.
    """
    from serial.tools import list_ports     # pyserial
    ports = list(list_ports.comports())
    arduino = [p for p in ports
               if p.vid in ARDUINO_VIDS
               or 'arduino' in (p.description or '').lower()
               or 'ttyACM' in p.device]
    found = arduino[0].device if len(arduino) == 1 else None
    return found, [(p.device, p.description) for p in ports]
