"""PanTiltService: Control 안에서 팬틸트를 돌리는 연결부 (예전 main.py 자리).

Control은 사건이 정해지면 이 객체의 메서드만 부른다.

    from hub.pantilt import PanTiltService

    pantilt = PanTiltService(port='auto', on_event=on_pantilt_event)   # Control 시작 시 한 번
    pantilt.preset(gen=3, seat=1, kind=1)          # 1번 좌석 쓰러짐 (1 쓰러짐, 2 질문, 3 자리비움)
    pantilt.track(gen=3, cx=.6, cy=.5, captured_at=t, frame_id=n)   # Vision track_obs가 올 때마다
    pantilt.home(gen=4)                            # 사건이 모두 끝나면
    pantilt.close()                                # Control 종료 시

동작 방식
- 메서드는 명령함(queue)에 넣기만 하고 바로 돌아온다. Control 쪽 코드가 시리얼 때문에 멈추지 않는다.
- 팬틸트 전용 스레드가 20ms마다 명령함을 비우고 Controller.tick()을 돌린다 (FSM, P 제어, 시리얼).
- Controller는 이 스레드에서만 건드리므로 Control 스레드와 상태가 꼬이지 않는다.
- Uno가 없거나 끊기면 SAFE로 두고 2초마다 다시 연결한다. Control은 계속 돌아간다.

on_event(event, fields)는 팬틸트 스레드에서 불린다. Control이 asyncio를 쓰면
loop.call_soon_threadsafe(...)로, 스레드를 쓰면 queue로 넘겨서 처리한다.
    'pan_tilt_state'  {state, pan, tilt, gen, error}   상태가 바뀔 때 (Console·DB용)
    'preset_saved'    {seat, pan, tilt}                프리셋 저장 시, 시작 직후 저장된 좌석마다
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from .constants import FsmState, Kind, PanTiltError, TrackStatus
from .controller import Controller
from .preset_store import PresetStore
from .serial_link import SerialLink, find_uno_port

log = logging.getLogger(__name__)

LOOP_S = .02              # 주기 20ms
SERIAL_RETRY_S = 2.0      # Uno 재연결 간격


class _NoDevice:
    """Uno 포트를 못 열었을 때 쓰는 빈 연결. Controller가 SAFE로 가서 재연결을 기다리게 한다."""
    ready = False

    def __init__(self, reason: str):
        self.error = reason
        self.pending: dict = {}

    def poll(self, now):
        return []

    def send(self, *args, **kwargs):
        raise OSError(self.error)

    def close(self):
        pass


class PanTiltService:
    def __init__(self, port: str | None = 'auto', *, presets_path=None,
                 on_event: Callable[[str, dict], object] | None = None,
                 simulate: bool = False, start: bool = True):
        """port: '/dev/ttyACM0', 'COM3', 'auto'(자동 찾기). simulate=True면 Uno 없이 모의 장치."""
        self.port, self.simulate = port, simulate
        self.on_event = on_event
        self.presets_store = PresetStore(presets_path)
        self._commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_open_error = None
        now = time.time()
        self.controller = Controller(self._open_link(), self.presets_store, now, notify=self._notify)
        self._last_retry = now
        self._announced = False
        if start:
            self.start()

    # ------------------------------------------------------------------ Control이 부르는 메서드
    def preset(self, gen: int, seat: int, kind: int) -> None:
        """사건 대상 좌석으로 이동하고 LED를 켠다. gen이 바뀔 때마다 부른다."""
        self._commands.put(('preset', int(gen), int(seat), int(kind)))

    def track(self, gen: int, cx: float, cy: float, captured_at: float,
              frame_id: int, found: bool = True) -> None:
        """팬틸트 카메라(cam2) 영상의 대상 좌표 (화면 비율 0~1, 중앙 0.5).

        captured_at은 cam2 Streamer가 기록한 촬영 시각(Unix 초)을 바꾸지 않고 넘긴다.
        """
        self._commands.put(('track', {
            'gen': int(gen), 'frame_id': int(frame_id), 'captured_at': float(captured_at),
            'status': TrackStatus.TRACKED if found else TrackStatus.LOST,
            'cx': float(cx), 'cy': float(cy)}))

    def home(self, gen: int) -> None:
        """사건이 없으면 HOME으로 돌아가고 LED를 끈다."""
        self._commands.put(('home', int(gen)))

    def jog(self, dpan: int, dtilt: int) -> None:
        """프리셋 등록용 수동 이동 (Console pan_tilt_jog). IDLE·HOLD_SEAT에서만 동작."""
        self._commands.put(('jog', int(dpan), int(dtilt)))

    def save_preset(self, seat: int) -> None:
        """현재 각도를 좌석 프리셋으로 저장 (Console preset_save). 끝나면 'preset_saved' 이벤트."""
        self._commands.put(('save', int(seat)))

    def set_vision_online(self, online: bool) -> None:
        """Vision 노드 연결 상태. 끊기면 추적을 멈추고 그 자리를 유지한다."""
        self._commands.put(('vision', bool(online)))

    def status(self) -> dict:
        """현재 상태 스냅숏 (Console 표시·디버그용)."""
        with self._lock:
            c = self.controller
            return {'state': c.state.name, 'gen': c.gen, 'seat': c.seat, 'kind': c.kind.name,
                    'error': c.error.name, 'pose': tuple(c.pose), 'target': tuple(c.target),
                    'serial_ok': c.serial_ok, 'vision_ok': c.vision_ok}

    def presets(self) -> dict[int, tuple[int, int]]:
        """등록된 좌석 프리셋 {좌석: (pan, tilt)}."""
        with self._lock:
            return dict(self.presets_store.seats)

    # ------------------------------------------------------------------ 시작·종료
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='pantilt', daemon=True)
        self._thread.start()

    def close(self) -> None:
        """정지(X), LED 끄기(L,O) 후 포트를 닫는다. 케이블이 빠진 경우엔 Uno의 3초 감시가 동작한다."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        with self._lock:
            try:
                now = time.time()
                self.controller.send('X', now=now)
                self.controller.send('L', 'O', now=now)
                time.sleep(.05)
            except (OSError, RuntimeError):
                pass
            self.controller.link.close()

    # ------------------------------------------------------------------ 내부
    def _open_link(self):
        if self.simulate:
            return SerialLink(None)
        port = self.port
        try:
            if port in (None, 'auto'):
                port, ports = find_uno_port()
                if port is None:
                    listed = ', '.join(f'{d}({desc})' for d, desc in ports) or '없음'
                    raise OSError(f'Uno 포트를 찾지 못함. 연결된 포트: {listed}')
            link = SerialLink(port)
            log.info('Uno 포트 열림: %s', port)
            self._last_open_error = None
            return link
        except ImportError:
            reason = 'pyserial이 없습니다: python -m pip install pyserial'
        except OSError as exc:
            reason = f'Uno 포트 열기 실패: {exc}'
            if 'Permission' in str(exc) or getattr(exc, 'errno', None) == 13:
                reason += ' (Linux: sudo usermod -aG dialout $USER 후 다시 로그인)'
        if reason != self._last_open_error:         # 같은 실패를 2초마다 반복해 찍지 않음
            log.warning(reason)
            self._last_open_error = reason
        return _NoDevice(reason)

    def _notify(self, event: str, now: float, **fields) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, fields)
        except Exception:                           # Control 콜백 오류로 팬틸트가 멈추지 않게
            log.exception('on_event 처리 오류: %s', event)

    def _apply(self, cmd: tuple, now: float) -> None:
        c = self.controller
        name, *args = cmd
        if name == 'preset':
            c.command_preset(*args, now)
        elif name == 'track':
            c.command_track(args[0], now)
        elif name == 'home':
            c.command_home(*args, now)
        elif name == 'jog':
            c.command_jog(*args, now)
        elif name == 'save':
            c.command_save(*args, now)
        elif name == 'vision':
            (c.vision_online if args[0] else c.vision_lost)(now)

    def _step(self, now: float) -> None:
        """20ms 한 주기. 테스트에서는 이 함수를 직접 불러 시간을 흉내 낸다."""
        with self._lock:
            c = self.controller
            if not self._announced:
                c.announce_presets(now)
                self._announced = True
            while True:
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._apply(cmd, now)
                except (OSError, RuntimeError) as exc:
                    c.safe(now, f'명령 처리 실패: {exc}', PanTiltError.SERIAL)
            c.tick(now)
            if c.state == FsmState.SAFE and now - self._last_retry >= SERIAL_RETRY_S:
                self._last_retry = now
                c.link.close()
                link = self._open_link()
                if isinstance(link, _NoDevice):
                    c.link = link                   # 아직 못 열었으면 SAFE 유지, 2초 뒤 다시
                else:
                    c.reconnect(link, now)          # INIT부터 다시, Control 요청은 유지

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self._step(started)
            except Exception:                       # 예상 못 한 오류도 로그만 남기고 계속
                log.exception('팬틸트 주기 처리 오류')
            self._stop.wait(max(0.0, LOOP_S - (time.time() - started)))


__all__ = ['PanTiltService', 'Kind', 'FsmState', 'PanTiltError']
