"""팬틸트 FSM과 영상 좌표 기반 P 제어 (Camera Controller 상세 설계).

Jarvis Control Service 안에서 동작한다. Control이 PanTiltService(service.py)를 통해 메서드를 부른다.
- 사건 확정·우선순위·대기열·gen 발급은 Control이 한다. 이 모듈은 받은 명령을 실행만 한다.
    command_preset(gen, seat, kind) -> 좌석 프리셋 이동 + LED
    command_track({gen, frame_id, captured_at, status, cx, cy}) -> P 제어 보정
    command_home(gen) -> HOME 복귀, LED 꺼짐
    command_jog / command_save -> 프리셋 등록 (Console 강의장 설정)
- FSM 상태가 바뀌면 notify('pan_tilt_state', ...)로 Control에 알린다.
- P 제어, 데드존 0.03, 1회 ±2°, 300ms 지난 좌표 폐기, 700ms 무결과 시 재탐색은 여기서 처리한다.
- Uno와는 시리얼 ASCII(IF-04)로만 통신한다 (serial_link.py).

시각은 모두 Unix 초(time.time())다. captured_at을 보내는 cam2 Streamer와 Control이
같은 강사 노트북에서 돌아가므로 같은 시계로 비교할 수 있다.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .constants import EVENT_PRESET_SAVED, EVENT_STATE, FsmState as State, Kind, PanTiltError, TrackStatus

log = logging.getLogger(__name__)

COLOR = {Kind.FALL: 'R', Kind.QUESTION: 'B', Kind.AWAY: 'G'}
ACTIVE = (State.MOVE_TO_SEAT, State.TRACKING, State.SEARCHING, State.HOLD_SEAT)

DEADZONE = .03             # 화면의 3% 이내 오차는 무시
MAX_STEP = 2               # 1회 보정 최대 ±2°
TRACK_HZ = 15              # 추적 명령 최대 15Hz
MAX_AGE = .3               # 촬영 후 300ms 지난 좌표는 버림
LOST_AFTER = .7            # 유효 좌표가 700ms 없으면 재탐색
SETTLE = .3                # 이동 출력 완료(D) 후 영상 안정화
CONFIRM_WAIT = 2.0         # 안정화 후 대상 확인 대기
SEARCH_OFFSET = 10         # 재탐색 Pan ±10°


@dataclass(frozen=True)
class TrackResult:
    frame_id: int
    target_generation: int
    cx: float
    cy: float
    captured_at: float     # Unix 초. 분석 완료 시각이 아니라 촬영 시각
    status: int = TrackStatus.TRACKED


def rounded(value: float) -> int:
    """0.5는 0에서 멀어지는 방향으로 반올림한다 (Python round()는 짝수 쪽으로 반올림)."""
    return int(math.copysign(math.floor(abs(value) + .5), value))


class Controller:
    def __init__(self, link, presets, now: float,
                 notify: Callable[..., object] | None = None):
        """link: SerialLink, presets: PresetStore, notify(event, now, **fields): Control에 알림."""
        self.link, self.presets = link, presets
        self.notify = notify or (lambda *a, **k: None)
        self.state = State.INIT
        self.state_since = now
        self.error = PanTiltError.NONE
        # Control이 요청한 목표: ('preset', gen, seat, kind) / ('home', gen) / None
        self.desired: tuple | None = None
        self.gen = 0
        self.seat: int | None = None
        self.kind = Kind.NONE
        self.vision_ok = True        # Vision이 끊기면 좌표가 올 수 없으므로 재탐색하지 않는다
        # 추적
        self.last_frame = -1
        self.latest_frame: TrackResult | None = None
        self.last_seen = now
        self.last_track_tx = -1e9
        # Uno 명령·출력 각도 추정
        self.config_seq = None
        self.move_seq = None
        self.move_done_at = None
        self.move_deadline = now + 5
        self.pose = list(presets.home)          # 출력 명령 각도 추정값. 실측값이 아니다.
        self.target = tuple(self.pose)
        self.interval = .02
        self.pose_tick = now
        self.history: deque = deque()          # (시각, (pan, tilt)) 최근 1초. 지연 보상용
        self.search_positions: deque = deque()
        self.initial_home = True

    # ------------------------------------------------------------------ 상태 보고
    def transition(self, state: State, now: float, reason: str) -> None:
        log.info('%s -> %s | %s', self.state.name, state.name, reason)
        self.state, self.state_since = state, now
        self.report(now)

    def report(self, now: float) -> None:
        pan, tilt = (max(0, min(255, int(v))) for v in self.pose)
        self.notify(EVENT_STATE, now, state=int(self.state), pan=pan, tilt=tilt,
                    gen=self.gen & 0xFFFF, error=int(self.error))

    @property
    def serial_ok(self) -> bool:
        return self.state != State.SAFE and bool(getattr(self.link, 'ready', False))

    # ------------------------------------------------------------------ Control 명령 (service.py가 호출)
    def command_preset(self, gen: int, seat: int, kind: int, now: float) -> None:
        desired = ('preset', gen, seat, kind)
        # 같은 명령이 다시 와도 (Control 재전송 등) 이동을 재시작하지 않는다.
        if desired == self.desired and self.state in ACTIVE:
            return
        self.desired = desired
        self.gen = gen
        # 초기화 중이면 첫 HOME과 안정화가 끝난 뒤 IDLE에서 적용한다.
        # 일반 HOME 복귀 중이면 바로 좌석으로 전환한다 (복귀 중 이벤트 발생).
        if self.state in (State.INIT, State.SAFE) or (
                self.state == State.RETURN_HOME and self.initial_home):
            return
        self._apply_desired(now)

    def command_home(self, gen: int, now: float) -> None:
        desired = ('home', gen)
        if desired == self.desired and self.state in (State.RETURN_HOME, State.IDLE):
            return
        self.desired = desired
        self.gen = gen
        if self.state in (State.INIT, State.SAFE):
            return
        self.home(now)

    def command_track(self, f: dict, now: float) -> None:
        # 지난 대상(gen)의 늦은 좌표와 LOST는 버린다. LOST는 last_seen을 갱신하지 않는다.
        if (f['gen'] != self.gen or self.seat is None or self.state not in ACTIVE
                or f['status'] != TrackStatus.TRACKED):
            return
        self.latest_frame = TrackResult(f['frame_id'], f['gen'], f['cx'], f['cy'],
                                        f['captured_at'], f['status'])

    def command_jog(self, dpan: int, dtilt: int, now: float) -> None:
        # 프리셋 등록용 수동 이동. 사건 처리 중에는 받지 않는다.
        if self.state not in (State.IDLE, State.HOLD_SEAT):
            log.warning('jog 무시: %s 상태', self.state.name)
            return
        goal = self.presets.clamp(self.target[0] + dpan, self.target[1] + dtilt)
        if goal != self.target:
            self.move('S', goal, now)

    def command_save(self, seat: int, now: float) -> None:
        if self.state in (State.INIT, State.SAFE):
            log.warning('preset_save 무시: %s 상태', self.state.name)
            return
        pan, tilt = self.presets.save(seat, *self.target, now=now)
        log.info('프리셋 저장 seat=%d pan=%d tilt=%d', seat, pan, tilt)
        self.notify(EVENT_PRESET_SAVED, now, seat=seat, pan=pan, tilt=tilt)

    def announce_presets(self, now: float) -> None:
        """시작 직후: 현재 상태와 저장된 좌석 프리셋을 알린다 (DB·Console has_preset 동기화용)."""
        self.report(now)
        for seat, (pan, tilt) in sorted(self.presets.seats.items()):
            self.notify(EVENT_PRESET_SAVED, now, seat=seat, pan=pan, tilt=tilt)

    def vision_online(self, now: float) -> None:
        self.vision_ok = True
        if self.error == PanTiltError.VISION_LOST:
            self.error = PanTiltError.NONE
            self.report(now)

    def vision_lost(self, now: float) -> None:
        """Vision 연결이 끊기면 추적만 멈추고 그 자리를 유지한다 (팀 결정 필요)."""
        self.vision_ok = False
        self.latest_frame = None
        if self.state in (State.TRACKING, State.SEARCHING):
            self.stop(now)
            self.search_positions.clear()
            self.error = PanTiltError.VISION_LOST
            self.transition(State.HOLD_SEAT, now, 'Vision 연결 끊김; 추적 중단')

    # ------------------------------------------------------------------ 동작
    def _apply_desired(self, now: float) -> None:
        d = self.desired
        if d is None:
            return
        if d[0] == 'home':
            self.home(now)
            return
        _, gen, seat, kind = d
        angles = self.presets.seat(seat)
        if angles is None:
            # 프리셋이 없는 좌석으로는 움직이지 않는다. 오류를 알리고 요청은 버린다.
            log.warning('좌석 %s 프리셋 없음', seat)
            self.desired = None
            self.error = PanTiltError.NO_PRESET
            self.report(now)
            return
        self.error = PanTiltError.NONE
        self.seat = seat
        try:
            self.kind = Kind(kind)
        except ValueError:
            self.kind = Kind.NONE
        self.reset_target()
        self.search_positions.clear()
        self.move('S', angles, now)              # 이동이 먼저: Uno 통신 오류 깜빡임을 해제
        self.send('L', COLOR.get(self.kind, 'O'), now=now)
        self.transition(State.MOVE_TO_SEAT, now, f'gen={gen} seat={seat} kind={self.kind.name}')

    def send(self, op, *args, now):
        return self.link.send(op, *args, now=now)

    def move(self, op: str, angles, now: float) -> None:
        angles = self.presets.clamp(*angles)
        self.move_seq = self.send(op, *(() if op == 'H' else angles), now=now)
        self.target = angles
        self.interval = .04 if op == 'T' else .02
        self.move_done_at = None
        # 전체 가동 범위를 기준으로 한 보수적 제한시간 (0~180이면 180° x 20ms + 1초)
        self.move_deadline = now + self.presets.max_span() * self.interval + 1.0
        if op == 'H' and self.initial_home:
            self.move_deadline += self.presets.control['initial_settle_seconds']

    def stop(self, now: float) -> None:
        self.send('X', now=now)
        self.target = tuple(self.pose)
        self.move_seq = None

    def reset_target(self) -> None:
        self.latest_frame = None
        self.last_frame = -1

    def home(self, now: float) -> None:
        self.seat, self.kind = None, Kind.NONE
        self.reset_target()
        self.search_positions.clear()
        self.move('H', self.presets.home, now)
        self.send('L', 'O', now=now)
        self.transition(State.RETURN_HOME, now, f'HOME 복귀 gen={self.gen}')

    def search(self, now: float) -> None:
        self.stop(now)
        pan, tilt = self.presets.seat(self.seat)
        self.search_positions = deque(self.presets.clamp(p, tilt)
                                      for p in (pan, pan - SEARCH_OFFSET, pan + SEARCH_OFFSET, pan))
        self.transition(State.SEARCHING, now, '대상 재탐색')
        self.next_search(now)

    def next_search(self, now: float) -> None:
        self.reset_target()
        self.move('S', self.search_positions.popleft(), now)

    def safe(self, now: float, reason: str, error: PanTiltError) -> None:
        if self.state == State.SAFE:
            return
        try:
            self.stop(now)
        except (OSError, RuntimeError):
            pass
        self.link.pending.clear()
        self.reset_target()
        self.error = error
        self.transition(State.SAFE, now, reason)

    def reconnect(self, link, now: float) -> None:
        """Uno 시리얼을 다시 연 뒤 호출. Control이 요청한 목표(desired)는 유지한다."""
        self.link = link
        self.config_seq = None
        self.move_seq = None
        self.history.clear()
        self.initial_home = True
        self.reset_target()
        self.transition(State.INIT, now, 'Uno 연결 재초기화; Control 요청 유지')

    # ------------------------------------------------------------------ P 제어
    def _estimate_pose(self, now: float) -> None:
        # 부동소수점 오차로 한 주기를 건너뛰면 추정 각도가 Uno보다 느려진다. 0.1ms 여유.
        if now - self.pose_tick >= self.interval - 1e-4:
            self.pose_tick = now
            for i in range(2):
                self.pose[i] += (self.target[i] > self.pose[i]) - (self.target[i] < self.pose[i])
        self.history.append((now, tuple(self.pose)))
        while self.history and now - self.history[0][0] > 1:
            self.history.popleft()

    def _valid_frame(self, now: float) -> TrackResult | None:
        f, self.latest_frame = self.latest_frame, None
        if f is None or self.seat is None:
            return None
        # 같은 프레임 재사용 금지. 큰 폭으로 줄면 Streamer 재시작으로 보고 받아들인다.
        repeated = f.frame_id <= self.last_frame and self.last_frame - f.frame_id < 1000
        if (f.status != TrackStatus.TRACKED or f.target_generation != self.gen or repeated
                or not math.isfinite(f.captured_at)
                or not 0 <= now - f.captured_at <= MAX_AGE
                or not all(math.isfinite(v) and 0 <= v <= 1 for v in (f.cx, f.cy))):
            return None
        # 이동·안정화 중 촬영된 영상으로 대상을 확인하지 않는다.
        if self.state != State.TRACKING:
            if self.move_done_at is None or f.captured_at < self.move_done_at + SETTLE:
                return None
        self.last_frame = f.frame_id
        return f

    def _track(self, f: TrackResult, now: float) -> None:
        self.last_seen = now
        if now - self.last_track_tx < 1 / TRACK_HZ:
            return
        # 지연 보상: 현재 각도가 아니라 프레임을 찍은 순간의 출력 각도를 기준으로 보정한다.
        candidates = [h for h in self.history if h[0] <= f.captured_at]
        if not candidates or f.captured_at - candidates[-1][0] > .06:
            return                           # 시간 기준이 불확실한 결과는 보정에 쓰지 않음
        base = candidates[-1][1]
        kp, direction = self.presets.control['kp'], self.presets.control['direction']
        corrections = []
        for i, error in enumerate((f.cx - .5, f.cy - .5)):
            delta = 0 if abs(error) < DEADZONE else rounded(direction[i] * kp[i] * error)
            corrections.append(max(-MAX_STEP, min(MAX_STEP, delta)))
        if corrections == [0, 0]:
            # 앞선 보정 이동이 진행 중이면 중심을 지나치지 않도록 정지한다.
            if tuple(self.pose) != self.target:
                self.stop(now)
                self.last_track_tx = now
            return
        # 보정하는 축만 촬영 시점 각도에 더한다. 보정량이 0인 축은 현재 목표를 유지한다.
        goal = self.presets.clamp(*(base[i] + corrections[i] if corrections[i]
                                    else self.target[i] for i in range(2)))
        if goal == self.target:
            return
        self.move('T', goal, now)
        self.last_track_tx = now

    # ------------------------------------------------------------------ 주기 처리 (20ms)
    def tick(self, now: float) -> None:
        self._estimate_pose(now)
        try:
            for line in self.link.poll(now):
                parts = line.split(',')
                if parts == ['R']:
                    self.config_seq = None
                    self.initial_home = True
                    self.reset_target()
                    self.history.clear()
                    self.transition(State.INIT, now, 'Uno 준비 완료/재부팅')
                elif parts[0] in ('K', 'D'):
                    seq = int(parts[1])
                    if seq == self.move_seq and len(parts) == 4:
                        self.pose = list(map(int, parts[2:]))
                        self.pose_tick = now
                        if parts[0] == 'D':
                            self.move_done_at = now
                    if parts[0] == 'K' and seq == self.config_seq and self.state == State.INIT:
                        self.error = PanTiltError.NONE
                        self.home(now)
            if self.link.error:
                self.safe(now, self.link.error, PanTiltError.SERIAL)
                return
            if self.state == State.SAFE:
                return
            if self.state == State.INIT:
                if self.link.ready and self.config_seq is None:
                    (p0, p1), (t0, t1) = self.presets.limits['pan'], self.presets.limits['tilt']
                    self.config_seq = self.send('C', p0, p1, t0, t1, *self.presets.home, now=now)
                if now - self.state_since > 5:
                    self.safe(now, '초기화 응답 시간 초과', PanTiltError.INIT_TIMEOUT)
                return
            if self.state == State.RETURN_HOME:
                settle = (self.presets.control['initial_settle_seconds']
                          if self.initial_home else SETTLE)
                if self.move_done_at is not None and now - self.move_done_at >= settle:
                    self.initial_home = False
                    self.transition(State.IDLE, now, 'HOME 출력 및 안정화 완료')
            elif self.state == State.IDLE:
                if self.desired and self.desired[0] == 'preset':
                    self._apply_desired(now)   # 초기화·복귀 중에 받은 좌석 명령
            elif self.state in ACTIVE:
                f = self._valid_frame(now)
                if f:
                    if self.state != State.TRACKING:
                        self.error = PanTiltError.NONE
                        self.transition(State.TRACKING, now, '대상 확인')
                    self._track(f, now)
                elif self.state == State.TRACKING and now - self.last_seen >= LOST_AFTER:
                    if self.vision_ok:
                        self.search(now)
                    else:
                        self.stop(now)
                        self.transition(State.HOLD_SEAT, now, '좌표 없음; Vision 연결 없음')
                elif (self.state in (State.MOVE_TO_SEAT, State.SEARCHING)
                      and self.move_done_at is not None
                      and now - self.move_done_at >= SETTLE + CONFIRM_WAIT):
                    if self.state == State.MOVE_TO_SEAT and self.vision_ok:
                        self.search(now)
                    elif self.state == State.SEARCHING and self.search_positions:
                        self.next_search(now)
                    else:
                        self.transition(State.HOLD_SEAT, now, '탐색 실패; 좌석 고정 감시')
            if (self.state in (State.MOVE_TO_SEAT, State.SEARCHING, State.RETURN_HOME)
                    and self.move_done_at is None and now > self.move_deadline):
                self.safe(now, '이동 출력 완료 응답 시간 초과', PanTiltError.MOVE_TIMEOUT)
        except (OSError, ValueError, RuntimeError) as exc:
            self.safe(now, str(exc), PanTiltError.SERIAL)
