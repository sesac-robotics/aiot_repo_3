"""카메라·Vision 없이 Control -> 팬틸트 -> Uno 전달을 확인하는 수동 시험.

Control이 실제로 쓰는 것과 같은 PanTiltService(hub/pantilt)를 띄우고,
키보드로 넣은 사건을 Control처럼 preset()/home()으로 넘긴다.
Uno에 보내는 시리얼 명령(TX)과 Uno 응답(RX)을 화면에 보여 준다.

실행 (이 파일이 있는 폴더에서, 또는 VS Code ▶)
    python3 manual_event_test.py                       # Uno 포트 자동 찾기
    python3 manual_event_test.py --port /dev/ttyACM0   # 포트 지정 (Windows: COM3)
    python3 manual_event_test.py --simulate            # Uno 없이
pantilt 폴더를 위로 올라가며 찾으므로 압축 폴더 맨 위, 저장소 tools/ 어디서든 동작한다.

입력
    1        쓰러짐  (LED 빨강)        좌석은 --seat (기본 1)
    2        질문    (LED 파랑)
    3        자리비움 (LED 초록)
    2 3      좌석 3에서 질문           (뒤 숫자 = 좌석 번호)
    d        현재 사건 완료 -> 다음 사건 또는 HOME
    h        모든 사건 지우고 HOME
    s        상태 보기
    q        종료

우선순위는 Control과 같다: 쓰러짐 > 질문 > 자리비움, 같은 등급은 먼저 들어온 순서.
카메라 좌표가 없으므로 좌석 도착 후 약 2.3초 뒤 HOLD_SEAT(좌석 고정)로 바뀌는 것이 정상이다.
--search를 주면 대신 재탐색(Pan -10° / +10°)까지 시험한다.
"""
import argparse
import logging
import queue
import sys
import threading
from pathlib import Path


def _add_hub_path():
    """파일을 어디에 두든 위로 올라가며 hub/pantilt를 찾는다."""
    here = Path(__file__).resolve().parent
    for folder in (here, *here.parents):
        for hub in (folder, folder / 'hub', folder / 'service' / 'jarvis_control_service' / 'hub'):
            if (hub / 'pantilt' / 'service.py').exists():
                sys.path.insert(0, str(hub))
                return
    sys.exit('hub/pantilt 폴더를 찾지 못했습니다. 저장소 구조 그대로 두고 실행하세요.\n'
             f'현재 위치: {here}')


_add_hub_path()

from pantilt import Kind, PanTiltService  # noqa: E402
from pantilt import serial_link  # noqa: E402

KIND_BY_KEY = {'1': Kind.FALL, '2': Kind.QUESTION, '3': Kind.AWAY}
KOR = {Kind.FALL: '쓰러짐', Kind.QUESTION: '질문', Kind.AWAY: '자리비움'}


class MiniControl:
    """Control의 우선순위·대기열·gen 규칙만 흉내 낸다 (시험용)."""

    def __init__(self, pantilt):
        self.pantilt = pantilt
        self.events = []           # [order, kind, seat]
        self.order = 0
        self.gen = 0
        self.active = None

    def _choose(self):
        best = min(self.events, key=lambda e: (e[1], e[0])) if self.events else None
        if best is self.active:
            return
        self.active = best
        self.gen += 1
        if best is None:
            print(f'  -> pantilt.home(gen={self.gen})', flush=True)
            self.pantilt.home(self.gen)
        else:
            _, kind, seat = best
            print(f'  -> pantilt.preset(gen={self.gen}, seat={seat}, kind={int(kind)} {KOR[kind]})',
                  flush=True)
            self.pantilt.preset(self.gen, seat, int(kind))

    def add(self, kind, seat):
        if any(e[1] == kind and e[2] == seat for e in self.events):
            print('  같은 좌석·같은 사건이 이미 있습니다', flush=True)
            return
        self.order += 1
        self.events.append([self.order, kind, seat])
        self._choose()
        if self.active and self.active[0] != self.order:
            print(f'  대기열에 추가 (현재 처리 중: {KOR[self.active[1]]} 좌석 {self.active[2]})',
                  flush=True)
        self.show_queue()

    def done(self):
        if not self.active:
            print('  처리 중인 사건이 없습니다', flush=True)
            return
        print(f'  완료: {KOR[self.active[1]]} 좌석 {self.active[2]}', flush=True)
        self.events.remove(self.active)
        self._choose()
        self.show_queue()

    def clear(self):
        n = len(self.events)
        self.events.clear()
        self._choose()
        print(f'  사건 {n}개 삭제', flush=True)
        self.show_queue()

    def show_queue(self):
        """처리 중인 사건과 그 뒤에 남은 사건을 우선순위 순서로 보여 준다."""
        rows = sorted(self.events, key=lambda e: (e[1], e[0]))
        print('  ┌ 사건 대기열 (쓰러짐 > 질문 > 자리비움, 같은 등급은 먼저 온 순서)', flush=True)
        if not rows:
            print('  └ 비어 있음 → HOME', flush=True)
            return
        for i, (order, kind, seat) in enumerate(rows):
            mark = '▶ 처리 중' if i == 0 else f'  대기 {i} '
            edge = '└' if i == len(rows) - 1 else '│'
            print(f'  {edge} {mark}  {KOR[kind]} 좌석 {seat}  (#{order}번째 입력)', flush=True)


def resolve_port(args):
    """--port가 없으면 Uno 포트를 찾는다. 못 찾으면 안내 후 종료."""
    if args.simulate or args.port:
        return args.port
    try:
        port, ports = serial_link.find_uno_port()
    except ImportError:
        sys.exit('pyserial이 없습니다. 터미널에서 설치하세요:\n'
                 '  python3 -m pip install pyserial\n'
                 '(Ubuntu에서 막히면: python3 -m pip install --user --break-system-packages pyserial)')
    if port is None:
        print('Uno 포트를 자동으로 고르지 못했습니다. 연결된 포트:', flush=True)
        for device, desc in ports:
            print(f'  {device}  {desc}', flush=True)
        if not ports:
            print('  (없음) USB 케이블을 확인하세요.', flush=True)
        sys.exit('포트를 지정해서 다시 실행하세요. 예: python3 manual_event_test.py --port /dev/ttyACM0\n'
                 'Uno 없이 시험하려면: python3 manual_event_test.py --simulate')
    print(f'Uno 포트: {port}', flush=True)
    return port


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--port', help='Uno 시리얼 포트 (생략하면 자동으로 찾음)')
    group.add_argument('--simulate', action='store_true', help='Uno 없이 모의 장치')
    parser.add_argument('--seat', type=int, default=1, help='좌석 번호를 생략했을 때 쓸 좌석')
    parser.add_argument('--search', action='store_true', help='좌표 없을 때 재탐색까지 시험')
    parser.add_argument('--presets', help='presets.json 경로 (기본: 자동으로 찾음)')
    args = parser.parse_args()
    port = resolve_port(args)

    # 팬틸트 상태 전환(INFO)과 Uno 시리얼 송수신(DEBUG: TX/RX)을 보여 준다.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s.%(msecs)03d %(message)s',
                        datefmt='%H:%M:%S')
    logging.getLogger(serial_link.__name__).setLevel(logging.DEBUG)

    safe_notice = {'shown': False}

    def on_event(event, fields):
        # Control이 받는 이벤트. 여기서는 SAFE 안내만 한다 (상태 전환은 위 로그에 보임).
        if event == 'pan_tilt_state' and fields['state'] == 7 and not safe_notice['shown']:
            safe_notice['shown'] = True
            print('  Uno 통신 오류(SAFE). 2초마다 다시 연결합니다. 포트·케이블을 확인하세요.', flush=True)
        elif event == 'pan_tilt_state' and fields['state'] != 7:
            safe_notice['shown'] = False

    pantilt = PanTiltService(port, presets_path=args.presets, simulate=args.simulate,
                             on_event=on_event)
    pantilt.set_vision_online(args.search)     # False면 좌표가 없을 때 재탐색 없이 좌석 고정
    mini = MiniControl(pantilt)
    seats = pantilt.presets()

    print(__doc__.split('입력')[1].split('우선순위는')[0], flush=True)
    print(f'좌석 프리셋: {seats}', flush=True)
    print('Uno 초기화(C → H) 후 IDLE이 되면 입력하세요.\n', flush=True)

    inputs = queue.Queue()
    threading.Thread(target=lambda: [inputs.put(line) for line in sys.stdin], daemon=True).start()

    try:
        while True:
            try:
                parts = inputs.get(timeout=.1).split()
            except queue.Empty:
                continue
            if not parts:
                continue
            key = parts[0].lower()
            try:
                if key in KIND_BY_KEY:
                    seat = int(parts[1]) if len(parts) > 1 else args.seat
                    if seat not in seats:
                        print(f'  좌석 {seat} 프리셋이 없습니다 (있는 좌석: {sorted(seats)})', flush=True)
                        continue
                    mini.add(KIND_BY_KEY[key], seat)
                elif key == 'd':
                    mini.done()
                elif key == 'h':
                    mini.clear()
                elif key == 's':
                    st = pantilt.status()
                    print(f'  FSM={st["state"]} gen={st["gen"]} seat={st["seat"]} error={st["error"]} '
                          f'출력 추정={st["pose"]} 목표={st["target"]} Uno={"정상" if st["serial_ok"] else "오류"}',
                          flush=True)
                    mini.show_queue()
                elif key == 'q':
                    break
                else:
                    print('  1/2/3 [좌석], d, h, s, q 중에서 입력하세요', flush=True)
            except ValueError as exc:
                print('  입력 오류:', exc, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        pantilt.close()


if __name__ == '__main__':
    main()
