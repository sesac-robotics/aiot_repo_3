"""Standalone fixed-camera runner. Deliberately does not start NodeClient/motors."""
import argparse
import json
import math
from pathlib import Path
import queue
import threading
import time
import uuid

from .config import load, save, registration, validate
from .core import Person, SeatMatcher, distance
from .decision_demo import DecisionDemo


class FrameSource:
    """Exactly one VideoCapture owner. Latest-frame queue, bounded memory."""
    def __init__(self, source, cv2):
        self.source, self.cv2 = source, cv2
        self.latest = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.eof = False
        self.error = None
        self.counter = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        cap = None
        try:
            cap = self.cv2.VideoCapture(self.source)
            if not cap.isOpened():
                raise RuntimeError(f'Cannot open {self.source}; stop the existing streamer using this camera')
            cap.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
            fps = cap.get(self.cv2.CAP_PROP_FPS)
            period = 1/(fps if math.isfinite(fps) and fps > 0 else 25)
            file_input = isinstance(self.source, str) and Path(self.source).is_file()
            target = time.monotonic()
            while not self.stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    if file_input:
                        self.eof = True
                        return
                    self.error = 'CAMERA_READ_FAILED: restart after checking USB/device'
                    return
                self.counter += 1
                packet = (self.counter, time.time(), time.monotonic(), frame)
                try:
                    self.latest.get_nowait()
                except queue.Empty:
                    pass
                self.latest.put_nowait(packet)
                if file_input:
                    target += period
                    self.stop.wait(max(0., target-time.monotonic()))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if cap is not None:
                cap.release()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def parse_args():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description='Fixed camera: local observations + central decision demo')
    p.add_argument('--source', default='0', help='Device number, /dev/videoN or video file')
    p.add_argument('--model', default='yolo11s-pose.pt', help='Existing pose weight path')
    p.add_argument('--allow-model-download', action='store_true', help='Explicitly permit Ultralytics model download')
    p.add_argument('--seats', default=str(root/'config/fixed_seats.local.json'))
    p.add_argument('--calibrate', action='store_true', help='Press 1..4 then click a person; no events during calibration')
    p.add_argument('--device', default=None, help='Ultralytics device: cpu or 0')
    p.add_argument('--imgsz', type=int, default=640)
    p.add_argument('--det-conf', type=float, default=.20)
    p.add_argument('--kpt-conf', type=float, default=.5)
    p.add_argument('--question-hold', type=float, default=2.)
    p.add_argument('--away-hold', type=float, default=10.)
    p.add_argument('--return-hold', type=float, default=2.)
    p.add_argument('--max-age', type=float, default=.8, help='Max capture-to-result seconds')
    p.add_argument('--log-dir', default=str(root/'logs/fixed_camera'))
    a = p.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (a.question_hold, a.away_hold, a.return_hold, a.max_age)):
        p.error('Hold/max-age values must be positive finite numbers')
    if not 0 < a.kpt_conf <= 1 or not 0 < a.det_conf <= 1:
        p.error('Confidence must be in (0,1]')
    if a.imgsz < 32 or a.imgsz % 32:
        p.error('imgsz must be a positive multiple of 32')
    if not a.allow_model_download and not Path(a.model).is_file():
        p.error('Model file missing: supply --model /absolute/path/yolo11s-pose.pt or --allow-model-download')
    return a


def main():
    a = parse_args()
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(f'{exc}. Activate your existing ultra environment and check README_FIXED_CAMERA.md')
    config = load(a.seats) if Path(a.seats).exists() else None
    if config is None and not a.calibrate:
        raise SystemExit('No seat calibration. Run with --calibrate first.')
    model = YOLO(a.model)
    if model.task != 'pose':
        raise SystemExit('Use a -pose.pt model')
    tracker = Path(__file__).with_name('bytetrack_fixed.yaml')
    source_arg = int(a.source) if a.source.isdigit() else a.source
    log_dir = Path(a.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    session = uuid.uuid4().hex
    obs_path = log_dir/f'{session}.observations.jsonl'
    event_path = log_dir/f'{session}.events.jsonl'
    matcher = SeatMatcher(config, a.kpt_conf) if config else None
    demo = DecisionDemo(a.question_hold, a.away_hold, a.return_hold)
    source = None
    armed = None
    selected = None
    samples = []
    sample_start = None
    current = []
    current_shape = None
    pending_click = None
    message = 'CALIBRATION ONLY: 1-4 then click student' if a.calibrate else 'LOCAL DEMO: NO NETWORK / NO MOTOR'
    win = 'Fixed Vision | 1-4 select | S screenshot | C complete | Q/ESC exit'
    def click(event, x, y, flags, param):
        nonlocal pending_click
        if event == cv2.EVENT_LBUTTONDOWN and a.calibrate and armed is not None:
            pending_click = (x, y)
    try:
        cv2.namedWindow(win, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(win, click)
        source = FrameSource(source_arg, cv2)
        last_good = time.monotonic()
        offline_sent = False
        with obs_path.open('w', encoding='utf-8') as obs_file, event_path.open('w', encoding='utf-8') as ev_file:
            def log_event(event):
                record = {'session': session, 'at': time.time(), **event}
                ev_file.write(json.dumps(record, ensure_ascii=False)+'\n')
                ev_file.flush()
                print(record, flush=True)
            while True:
                try:
                    fid, captured, mono, frame = source.latest.get(timeout=.05)
                except queue.Empty:
                    if source.error:
                        raise RuntimeError(source.error)
                    if source.eof:
                        break
                    if matcher and time.monotonic()-last_good > a.max_age and not offline_sent:
                        log_event({'action': 'source_stale', 'seats': matcher.unknown('NO_FRESH_FRAME')})
                        matcher.reset()
                        # Do not advance any event timer during missing frames.
                        offline_sent = True
                    if cv2.waitKey(1) & 255 in (27, ord('q'), ord('Q')):
                        break
                    continue
                last_good = time.monotonic()
                offline_sent = False
                h, w = frame.shape[:2]
                if w > 960:
                    frame = cv2.resize(frame, (960, round(h*960/w)))
                h, w = frame.shape[:2]
                aspect = w/h
                current_shape = (w, h)
                if config:
                    validate(config, aspect)
                options = dict(persist=True, tracker=str(tracker), conf=a.det_conf,
                               imgsz=a.imgsz, verbose=False)
                if a.device is not None:
                    options['device'] = a.device
                result = model.track(frame, **options)[0]
                current = []
                if result.boxes is not None and result.keypoints is not None:
                    boxes = result.boxes.xyxyn.cpu().tolist()
                    kpts = result.keypoints.data.cpu().numpy().copy()
                    kpts[:, :, 0] /= w
                    kpts[:, :, 1] /= h
                    ids = result.boxes.id
                    ids = ids.int().cpu().tolist() if ids is not None else [None]*len(boxes)
                    confs = result.boxes.conf.cpu().tolist()
                    current = [Person(tuple(b), k.tolist(), tid, conf)
                               for b, k, tid, conf in zip(boxes, kpts, ids, confs)]
                now = time.monotonic()
                fresh = now-mono <= a.max_age
                canvas = result.plot()
                if pending_click is not None:
                    x, y = pending_click
                    pending_click = None
                    candidates = [(distance((x/w, y/h), p.anchor(a.kpt_conf), aspect), p)
                                  for p in current if p.anchor(a.kpt_conf) is not None]
                    candidates.sort(key=lambda z: z[0])
                    if (fresh and candidates and candidates[0][0] < .18
                            and (len(candidates) == 1 or candidates[1][0]-candidates[0][0] > .025)
                            and candidates[0][1].track_id is not None):
                        selected = candidates[0][1].track_id
                        samples = []
                        sample_start = now
                        message = f'Seat {armed}: collecting normal seated shoulders (2s)'
                    else:
                        message = 'Cannot select: click close to clear shoulder midpoint, not overlapping people'
                if selected is not None:
                    person = next((p for p in current if p.track_id == selected), None)
                    anchor = person.anchor(a.kpt_conf) if person else None
                    scale = person.scale(aspect, a.kpt_conf) if person else None
                    if not fresh or anchor is None or scale is None:
                        selected = None
                        samples = []
                        message = 'Registration cancelled: shoulders/track lost; click again'
                    else:
                        samples.append((*anchor, scale))
                        if now-sample_start >= 2:
                            try:
                                entry = registration(samples, aspect)
                                cfg = config or {'version': 1, 'camera_id': 1, 'aspect': aspect, 'seats': {}}
                                # A person cannot register multiple seats without an explicit new selection.
                                for s, old in cfg['seats'].items():
                                    if str(s) != str(armed) and distance(old['anchor'], entry['anchor'], aspect) < .04:
                                        raise ValueError(f'Too close to seat {s}; choose correct student')
                                cfg['seats'][str(armed)] = entry
                                cfg['source_note'] = a.source
                                save(a.seats, cfg)
                                config = cfg
                                matcher = SeatMatcher(config, a.kpt_conf)
                                message = f'Seat {armed} saved. Next 1-4 or restart without --calibrate'
                                armed = None
                            except ValueError as exc:
                                message = str(exc)
                            selected = None
                            samples = []
                rows = (matcher.observe(current, mono) if fresh else matcher.unknown('STALE_INFERENCE')) if matcher else []
                obs = {'schema': 'fixed_seat_obs_v1', 'camera_id': 1, 'session': session,
                       'frame_id': fid, 'captured_at': captured, 'processed_at': time.time(),
                       'image_size': [w, h], 'seats': rows}
                obs_file.write(json.dumps(obs, ensure_ascii=False)+'\n')
                obs_file.flush()
                if not a.calibrate:
                    for event in demo.process(obs, mono):
                        # Demo clock uses monotonic evidence; log capture/Unix for correlation.
                        log_event({'demo_clock': 'monotonic', **event})
                if config:
                    for s, entry in config['seats'].items():
                        x, y = entry['anchor']
                        cv2.circle(canvas, (int(x*w), int(y*h)), 7, (0,255,255), 2)
                        cv2.putText(canvas, f'S{s}', (int(x*w)+8, int(y*h)), 0, .6, (0,255,255), 2)
                for p in current:
                    anchor = p.anchor(a.kpt_conf)
                    if anchor:
                        cv2.circle(canvas, (int(anchor[0]*w), int(anchor[1]*h)), 4, (0,255,255), -1)
                for n, row in enumerate(rows):
                    text = f"S{row['seat']} P={row['present']} H={row['hand_up']} ID={row['track_id']} {row['reason']}"
                    cv2.putText(canvas, text, (8, 23+n*23), 0, .48, (0,255,255), 1)
                order = demo.queue()
                summary = ' | '.join(f"S{e['seat']} {e['kind']}" for e in order) or 'none'
                cv2.putText(canvas, 'DEMO QUEUE: '+summary, (8, h-45), 0, .5, (0,255,255), 1)
                cv2.putText(canvas, message[:120], (8, h-20), 0, .42, (0,255,255), 1)
                cv2.imshow(win, canvas)
                key = cv2.waitKey(1) & 255
                if key in (27, ord('q'), ord('Q')) or cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if a.calibrate and key in (ord('1'), ord('2'), ord('3'), ord('4')):
                    armed = key-ord('0')
                    selected = None
                    message = f'Seat {armed}: click near shoulder midpoint, sit normally'
                if key in (ord('c'), ord('C')) and not a.calibrate and order:
                    e = order[0]
                    if e['kind'] == 'question':
                        log_event(demo.close(e['seat'], 'question', time.monotonic()))
                if key in (ord('s'), ord('S')):
                    path = log_dir/f'capture_{time.time_ns()}.png'
                    if not cv2.imwrite(str(path), canvas):
                        raise RuntimeError('Cannot save screenshot')
                    print(f'Saved {path}', flush=True)
    finally:
        if source:
            source.close()
        cv2.destroyAllWindows()
        print(f'Observations: {obs_path}\nDemo events: {event_path}', flush=True)
