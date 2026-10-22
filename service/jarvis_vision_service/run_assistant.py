"""Vision node: hub UDP frames -> pose observations / track_obs over TCP v3.

Calibration uses fixed_camera/app.py --calibrate (on the PC with the fixed camera) before starting the streamers.
Seats: every seat registered in fixed_seats.local.json (--only-seat N for a one-seat test).
cam2 tracking refuses multi-person frames (no cross-camera identity yet).

AI 조교(LLM) 기능 (main 에서 합침, llm/ 폴더):
- explain_request 를 받으면 LLM(Gemini 기본, Ollama 백업) 설명을 explain_text 로 돌려준다.
- [질의] ask_request 를 받으면 ask.py 가 LLM tool calling 으로 답을 만들어 ask_answer 로 돌려준다
  (도구는 tool_call 로 control 에 실행을 맡긴다).
- [리포트] report_write 를 받으면 report.py 가 오늘 숫자를 읽고 요약·살펴볼 점을 써서 report_text 로 돌려준다.
"""
import argparse
import collections
import json
import logging
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# AI 조교(LLM) 모듈은 llm/ 폴더에 있다 (llm.py, ask.py, report.py, assistant_config.py, config/assistant.yaml)
sys.path.insert(0, str(Path(__file__).resolve().parent / "llm"))
from common.config import load_config
from common.node_client import NodeClient
from common.protocol import VISION_NODE, VIDEO_HEADER, VISION_VIDEO_PORTS
from fixed_camera.config import load
from fixed_camera.core import Person, SeatMatcher, valid_point
from fixed_camera.seat_report import SeatReport

import ask                                     # [질의] 강사 자연어 질의 + tool calling
import llm                                     # AI 조교 설명 (Gemini → Ollama → 기본 문구)
import report                                  # [리포트] 수업 종료 리포트 문장
from assistant_config import load_assistant_config

log = logging.getLogger('vision')
ROOT = Path(__file__).resolve().parent
WARN_S = 10.0           # 같은 경고(버린 프레임, 처리 오류, 영상 비율)를 다시 찍기까지 최소 간격
MAX_LAG_S = .8          # 수신 지연(시계 차이를 뺀 값)이 이보다 크면 밀린 프레임으로 보고 버린다 (Control OBS_GAP_S 와 같게)
CLOCK_WINDOW_S = 10.0   # 카메라 PC 시계 차이를 이 구간의 최소 (수신 시각 - 촬영 시각) 으로 추정한다
MIN_SEAT_FPS = 2.0      # Control 판정은 관측 간격 0.8초(약 1.25 fps)를 넘으면 처음부터 다시 센다 → 여유를 두고 경고

LLM_CFG = load_assistant_config()                                     # config/assistant.yaml + .env
explain_slots = threading.Semaphore(int(LLM_CFG.get("max_concurrent", 2)))   # 동시 설명 요청 수 제한


def explain(client: NodeClient, req: dict):
    """LLM 으로 상황 설명을 만들어 explain_text 로 돌려준다 (Gemini -> Ollama -> 기본 문구).
    llm.explain_event 는 예외를 던지지 않고 항상 (문자열, "gemini"|"ollama"|"default") 를 준다.
    text 는 꼭 문자열로 보낸다 (control 이 문자열이 아니면 버림)."""
    with explain_slots:                     # 사건이 몰려도 LLM 을 동시에 너무 많이 부르지 않게
        try:
            text, mode = llm.explain_event(req, LLM_CFG)
        except Exception:                   # 혹시 모를 오류에도 기본 문구는 꼭 보낸다
            logging.getLogger("llm").exception("설명 실패 event=%s", req.get("event_id"))
            text, mode = llm.default_text(req), "default"
    value = {"event_id": req["event_id"], "text": str(text), "mode": mode}
    if "seq" in req:                        # control 이 늦게 온 옛 응답을 거를 수 있게 그대로 돌려준다
        value["seq"], value["situation"] = req["seq"], req.get("situation")
    client.send("explain_text", value=value)
    log.info("-> explain_text event=%s %s [%s] %s", req["event_id"], req.get("situation", "detect"), mode, text)


def nose_target(people):
    """No cross-camera identity claim: one visible person is required."""
    if len(people) != 1 or not valid_point(people[0].keypoints, 0):
        return None
    return tuple(people[0].keypoints[0][:2])


class ClockSync:
    """카메라 PC 시계를 이 PC 시계로 옮긴다 (NTP 가 안 맞아도 동작하게).

    raw = 수신 시각 - 촬영 시각 = 네트워크·처리 지연 + 두 PC 시계 차이.
    같은 LAN 에서 가장 빨리 온 프레임의 지연은 수 ms 이므로, 최근 CLOCK_WINDOW_S 동안의 최소 raw 를
    시계 차이로 본다. 촬영 시각에 이 값을 더하면 이 PC 시계 기준 촬영 시각이 되고,
    lag = raw - 최소 raw 는 '평소보다 얼마나 늦게 왔는지'(밀림)만 남는다.
    10/22: 고정 카메라 PC 가 0.65초 빨라 모든 cam1 프레임이 -0.2~0.8초 검사에 걸려 버려졌다.
    카메라 시계가 갑자기 뒤로 가면(NTP 맞춤) 최대 CLOCK_WINDOW_S 동안 lag 가 커 보일 수 있다."""

    def __init__(self, window=CLOCK_WINDOW_S):
        self.window = window
        self.mins = collections.deque()          # (수신 시각, raw) - raw 가 커지는 순서로만 남긴다 (구간 최소)

    def update(self, now, captured):
        raw = now - captured
        while self.mins and self.mins[-1][1] >= raw:
            self.mins.pop()
        self.mins.append((now, raw))
        while self.mins[0][0] < now - self.window:
            self.mins.popleft()
        offset = self.mins[0][1]
        return captured + offset, raw - offset, offset     # (이 PC 기준 촬영 시각, 밀림, 시계 차이)


class Receiver:
    def __init__(self, cam, bind):
        self.cam = cam
        self.latest = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, VISION_VIDEO_PORTS[cam]))
        self.sock.settimeout(.2)
        self.clock = ClockSync()
        self.offset = 0.                          # 지금 보정 중인 시계 차이 (초, 상태 로그용)
        self.stale, self.worst_lag, self.warned_at = 0, 0., 0.   # 밀려서 버린 프레임 (경고용)
        self.offset_logged = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def _drop_stale(self, lag):
        """밀린 프레임은 조용히 버리지 않고 10초마다 몇 개 버렸는지 알린다."""
        self.stale += 1
        self.worst_lag = max(self.worst_lag, lag)
        now = time.time()
        if now - self.warned_at >= WARN_S:
            log.warning('cam%d 프레임 %d개 버림: 평소보다 최대 %.2f초 늦게 도착 (허용 %.1f초). '
                        '네트워크나 카메라 PC 가 밀리고 있습니다', self.cam, self.stale, self.worst_lag, MAX_LAG_S)
            self.stale, self.worst_lag, self.warned_at = 0, 0., now

    def _log_offset(self, offset):
        """시계 차이가 크게 바뀌었을 때만 알린다 (보정은 계속 됨, NTP 를 맞추면 0 에 가까워진다)."""
        if self.offset_logged is None or abs(offset - self.offset_logged) >= .1:
            self.offset_logged = offset
            (log.warning if abs(offset) >= .2 else log.info)(
                'cam%d 카메라 PC 시계 차이 %+.2f초를 보정해서 씁니다%s', self.cam, -offset,
                ' (카메라 PC 를 NTP 로 맞추면 좋습니다)' if abs(offset) >= .2 else '')

    def run(self):
        last_id, last_capture = None, 0.
        while not self.stop.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(data) <= VIDEO_HEADER.size or data[0] != self.cam:
                continue
            cam, fid, captured = VIDEO_HEADER.unpack_from(data)
            # 순서 확인은 카메라 PC 시계 그대로 (같은 시계끼리 비교). Allow a streamer's frame counter
            # to restart, using newer capture time.
            if captured <= last_capture or (fid == last_id):
                continue
            last_id, last_capture = fid, captured
            local, lag, self.offset = self.clock.update(time.time(), captured)
            self._log_offset(self.offset)
            if lag > MAX_LAG_S:
                self._drop_stale(lag)
                continue
            try:
                self.latest.get_nowait()
            except queue.Empty:
                pass
            # 이후(판정·seat_obs·track_obs)는 이 PC 시계 기준 촬영 시각을 쓴다 → Control 의 시각 검사와도 맞는다
            self.latest.put_nowait((fid, local, data[VIDEO_HEADER.size:]))

    def close(self):
        self.stop.set()
        self.sock.close()
        self.thread.join(timeout=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--server')
    ap.add_argument('--port', type=int)
    ap.add_argument('--bind', default='0.0.0.0')
    ap.add_argument('--model', required=True, help='Existing pose model path (no automatic download)')
    ap.add_argument('--device', default='0')
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--seats', default=str(ROOT/'config/fixed_seats.local.json'))
    ap.add_argument('--record-dir', default=None, help='Optional local cam1/cam2 MP4 and timestamp logs')
    ap.add_argument('--record-keypoints', default=None,
                    help='cam1 keypoints + seat rows per frame to this JSONL (numbers only, no image). '
                         'Replay it to tune seat rules without asking students to sit again')
    ap.add_argument('--only-seat', type=int, choices=(1, 2, 3, 4), default=None,
                    help='Use just this registered seat (one-seat test). Default: all registered seats')
    args = ap.parse_args()
    if not Path(args.model).is_file():
        ap.error('Pose model file not found; supply its actual local path')
    cfg = load_config(control_host=args.server, control_port=args.port)
    calibration = load(args.seats)
    if args.only_seat is not None:
        key = str(args.only_seat)
        if key not in calibration['seats']:
            ap.error(f'Register seat {key} with python3 -m fixed_camera.app --calibrate first')
        calibration['seats'] = {key: calibration['seats'][key]}
    import cv2
    import numpy as np
    from ultralytics import YOLO
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s: %(message)s')
    if args.record_dir:
        # 팀 원칙은 "영상은 저장하지 않음"이다. 시험용 옵션이므로 켤 때마다 분명히 알린다.
        log.warning('녹화 켜짐: cam1·cam2 수업 영상을 %s 에 MP4 로 저장합니다. 시험용으로만 쓰고 '
                    '시험이 끝나면 지우세요 (git 에 올리지 않음)', args.record_dir)
    session = str(uuid.uuid4())
    stop = threading.Event()
    lock = threading.Lock()
    target = {'seat': None, 'gen': 0, 'since': time.time()}
    failures = queue.Queue()

    workers = {}                                  # [질의] asker, [리포트] reporter (client 를 만든 뒤 채움)

    def on_message(msg):
        """control 이 보낸 메시지 (수신 스레드에서 불림)."""
        for worker_ in workers.values():          # [질의] ask_request / tool_result, [리포트] report_write
            if worker_.on_message(msg):
                return
        v = msg['value']
        if msg['type'] == 'track_target':
            with lock:
                target.update(seat=v.get('seat'), gen=v['gen'], since=time.time())
            log.info('Target %s', target)
        elif msg['type'] == 'explain_request':
            # LLM 호출은 느리므로 따로 돌린다 (수신 스레드를 막으면 track_target 이 늦게 온다)
            threading.Thread(target=explain, args=(client, v), daemon=True).start()

    client = NodeClient(VISION_NODE, cfg['control_host'], int(cfg['control_port']), on_message=on_message)
    workers['asker'] = ask.AskWorker(client, LLM_CFG, on_answer=lambda req, res: log.info(
        "-> ask_answer #%s [%s %ss, 도구 %d번] %s", req['ask_id'], res['mode'], res['took_s'],
        len(res['tools']), res['text'][:80]))
    workers['reporter'] = report.ReportWorker(client, LLM_CFG, on_done=lambda req, res: log.info(
        "-> report_text #%s [%s %ss] %s", req['report_id'], res['mode'], res['took_s'], res['summary'][:80]))
    log.info("AI 조교: gemini=%s, ollama=%s",
             LLM_CFG['gemini_model'] if LLM_CFG.get('gemini_api_key') else '키 없음(건너뜀)', LLM_CFG['ollama_model'])
    if LLM_CFG.get("ollama_warmup", True):
        # Ollama 모델을 미리 GPU 에 올려 둔다 (첫 설명이 느리지 않게). 감지 루프를 막지 않게 따로 돌린다.
        threading.Thread(target=llm.warmup, args=(LLM_CFG,), daemon=True).start()
    receivers = []
    threads = []

    def worker(receiver, model):
        cam = receiver.cam
        rec = {'writer': None, 'timeline': None, 'on': bool(args.record_dir)}
        kpt_log = open(args.record_keypoints, 'a', encoding='utf-8') if (args.record_keypoints and cam == 1) else None
        matcher = SeatMatcher(calibration)
        seat_report = SeatReport()                # cam1: 좌석별 판정 요약 (10초마다 로그)
        stats = {'count': 0, 'started': time.time()}
        warned = {'error': 0., 'aspect': 0.}

        def warn_once(key, *msg, exc=False):
            now = time.time()
            if now - warned[key] >= WARN_S:
                warned[key] = now
                (log.exception if exc else log.warning)(*msg)

        def record(frame, fid, captured, w, h):
            if rec['writer'] is None:
                folder = Path(args.record_dir)/session
                folder.mkdir(parents=True, exist_ok=True)
                writer = cv2.VideoWriter(str(folder/f'cam{cam}.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), 15., (w, h))
                if not writer.isOpened():
                    # 녹화는 선택 기능이다. 못 열면 녹화만 끄고 분석은 계속한다.
                    log.error('cam%d 녹화 파일을 열 수 없어 녹화를 끕니다 (%s)', cam, folder)
                    rec['on'] = False
                    return
                rec['writer'], rec['timeline'] = writer, (folder/f'cam{cam}.jsonl').open('w')
            rec['writer'].write(frame)
            rec['timeline'].write(json.dumps({'frame_id': fid, 'captured_at': captured})+'\n')
            rec['timeline'].flush()

        def handle(fid, captured, jpeg):
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return
            h, w = frame.shape[:2]
            if rec['on']:
                record(frame, fid, captured, w, h)
            with lock:
                active = dict(target)
            opts = dict(conf=.25, imgsz=args.imgsz, device=args.device, verbose=False)
            aspect_ok = abs(calibration['aspect'] - w/h) <= .01
            if cam == 1 and not aspect_ok:
                # 등록할 때와 영상 비율이 다르면 좌석 위치가 맞지 않는다. 멈추지 않고 '모름'으로 보낸다.
                warn_once('aspect', 'cam1 영상 비율 %.3f 이 등록 비율 %.3f 과 다릅니다. '
                          'python3 -m fixed_camera.app --calibrate 로 좌석을 다시 등록하세요 (그동안 좌석은 모름)',
                          w/h, calibration['aspect'])
                result = None
            elif cam == 1:
                result = model.track(frame, persist=True,
                    tracker=str(ROOT/'fixed_camera/bytetrack_fixed.yaml'), **opts)[0]
            else:
                result = model.predict(frame, **opts)[0]
            people = []
            if result is not None and result.boxes is not None and result.keypoints is not None:
                boxes = result.boxes.xyxyn.cpu().tolist()
                kpts = result.keypoints.data.cpu().numpy().copy()
                kpts[:, :, 0] /= w
                kpts[:, :, 1] /= h
                ids = result.boxes.id
                ids = ids.int().cpu().tolist() if ids is not None else [None]*len(boxes)
                people = [Person(tuple(b), k.tolist(), tid, confidence)
                    for b, k, tid, confidence in zip(boxes, kpts, ids, result.boxes.conf.cpu().tolist())]
            now = time.time()
            if cam == 1:
                if not aspect_ok:
                    rows = matcher.unknown('ASPECT_CHANGED')
                elif now-captured <= .8:
                    rows = matcher.observe(people, time.monotonic())
                else:
                    rows = matcher.unknown('STALE')
                if kpt_log:
                    # 영상 없이 숫자만: 다시 돌려 보면 같은 판정을 재현할 수 있다 (fixed_camera.core 로 replay)
                    kpt_log.write(json.dumps({'frame_id': fid, 'captured_at': captured, 'processed_at': now,
                        'people': [{'box': [round(v, 4) for v in p.box], 'id': p.track_id, 'conf': round(p.confidence, 3),
                                    'kpts': [[round(v, 4) for v in kp] for kp in p.keypoints]} for p in people],
                        'seats': rows}, ensure_ascii=False) + '\n')
                line = seat_report.add(rows, now)
                if line:
                    log.info('좌석 판정 %s', line)
                client.send('seat_obs', value={'session': session, 'frame_id': fid,
                    'captured_at': captured, 'processed_at': now, 'seats': rows})
            elif active['seat'] is not None and captured >= active['since']:
                nose = nose_target(people) if now-captured <= .3 else None
                client.send('track_obs', seat=active['seat'], value={'gen': active['gen'], 'frame_id': fid,
                    'captured_at': captured, 'status': 'TRACKED' if nose else 'LOST',
                    **({'cx': nose[0], 'cy': nose[1]} if nose else {})})
            stats['count'] += 1
            if now-stats['started'] >= 2:
                fps = stats['count']/(now-stats['started'])
                client.set_status(**{f'cam{cam}_fps': fps})
                log.info('cam%d analysis %.1f fps, age %.0f ms (시계 차이 %+.2fs 보정)', cam, fps,
                         (now-captured)*1000, -receiver.offset)
                if cam == 1 and fps < MIN_SEAT_FPS:
                    log.warning('cam1 분석 %.1f fps: %.0f fps 보다 느리면 손들기·자리비움이 확정되지 않을 수 있습니다 '
                                '(--imgsz 를 줄이거나 GPU 를 쓰세요)', fps, MIN_SEAT_FPS)
                stats['count'], stats['started'] = 0, now

        try:
            while not stop.is_set():
                try:
                    fid, captured, jpeg = receiver.latest.get(timeout=.2)
                except queue.Empty:
                    client.set_status(**{f'cam{cam}_fps': 0.})
                    continue
                try:
                    handle(fid, captured, jpeg)
                except Exception:
                    # 한 카메라의 한 프레임 오류로 Vision 전체(다른 카메라 포함)를 멈추지 않는다.
                    warn_once('error', 'cam%d 프레임 처리 오류 - 이 프레임은 건너뜀', cam, exc=True)
        except Exception as exc:
            log.exception('cam%d stopped', cam)
            failures.put(exc)
            stop.set()
        finally:
            if rec['writer'] is not None:
                rec['writer'].release()
            if kpt_log is not None:
                kpt_log.close()
            if rec['timeline'] is not None:
                rec['timeline'].close()

    try:
        # Separate model instances avoid shared tracker/inference state between cameras.
        models = [YOLO(args.model), YOLO(args.model)]
        for model in models:
            if model.task != 'pose':
                raise ValueError('Use a pose model, e.g. yolo11n-pose.pt')
            model.predict(np.zeros((480, 640, 3), dtype=np.uint8), device=args.device,
                          imgsz=args.imgsz, verbose=False)
        for cam in (1, 2):
            receivers.append(Receiver(cam, args.bind))
        client.start()
        for receiver, model in zip(receivers, models):
            thread = threading.Thread(target=worker, args=(receiver, model), daemon=True)
            thread.start()
            threads.append(thread)
        log.info('Seats %s: hub -> UDP 5021/5022. cam2 must contain ONE person. Ctrl+C stops.',
                 ', '.join(sorted(calibration['seats'])))
        while not stop.wait(.2):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for receiver in receivers:
            receiver.close()
        for thread in threads:
            thread.join(timeout=5)
        client.stop()
    if not failures.empty():
        raise SystemExit(f'Vision failed: {failures.get()}')


if __name__ == '__main__':
    main()
