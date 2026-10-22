"""Vision node: hub UDP frames -> pose observations / track_obs over TCP v3.

Calibration uses run_fixed_vision.py before starting the streamers.
This test runner tracks only seat 1 and refuses multi-person cam2 frames.
"""
import argparse
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
from common.config import load_config
from common.node_client import NodeClient
from common.protocol import VISION_NODE, VIDEO_HEADER, VISION_VIDEO_PORTS
from fixed_camera.config import load, validate
from fixed_camera.core import Person, SeatMatcher, valid_point

log = logging.getLogger('vision')
ROOT = Path(__file__).resolve().parent


def nose_target(people):
    """No cross-camera identity claim: one visible person is required."""
    if len(people) != 1 or not valid_point(people[0].keypoints, 0):
        return None
    return tuple(people[0].keypoints[0][:2])


class Receiver:
    def __init__(self, cam, bind):
        self.cam = cam
        self.latest = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind, VISION_VIDEO_PORTS[cam]))
        self.sock.settimeout(.2)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

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
            if not -.2 <= time.time()-captured <= .8:
                continue
            # Allow a streamer's frame counter to restart, using newer capture time.
            if captured <= last_capture or (fid == last_id):
                continue
            last_id, last_capture = fid, captured
            try:
                self.latest.get_nowait()
            except queue.Empty:
                pass
            self.latest.put_nowait((fid, captured, data[VIDEO_HEADER.size:]))

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
    args = ap.parse_args()
    if not Path(args.model).is_file():
        ap.error('Pose model file not found; supply its actual local path')
    cfg = load_config(control_host=args.server, control_port=args.port)
    calibration = load(args.seats)
    if '1' not in calibration['seats']:
        ap.error('Register seat 1 with run_fixed_vision.py --calibrate first')
    calibration['seats'] = {'1': calibration['seats']['1']}
    import cv2
    import numpy as np
    from ultralytics import YOLO
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s: %(message)s')
    session = str(uuid.uuid4())
    stop = threading.Event()
    lock = threading.Lock()
    target = {'seat': None, 'gen': 0, 'since': time.time()}
    failures = queue.Queue()

    def on_message(msg):
        v = msg['value']
        if msg['type'] == 'track_target':
            with lock:
                target.update(seat=v.get('seat'), gen=v['gen'], since=time.time())
            log.info('Target %s', target)
        elif msg['type'] == 'explain_request':
            client.send('explain_text', value={'event_id': v['event_id'],
                'text': f"{v['seat']}번 좌석 {v['kind']}", 'mode': 'default'})

    client = NodeClient(VISION_NODE, cfg['control_host'], int(cfg['control_port']), on_message=on_message)
    receivers = []
    threads = []

    def worker(receiver, model):
        writer = timeline = None
        matcher = SeatMatcher(calibration)
        count, started = 0, time.time()
        try:
            while not stop.is_set():
                try:
                    fid, captured, jpeg = receiver.latest.get(timeout=.2)
                except queue.Empty:
                    client.set_status(**{f'cam{receiver.cam}_fps': 0.})
                    continue
                frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                h, w = frame.shape[:2]
                if args.record_dir:
                    if writer is None:
                        folder = Path(args.record_dir)/session
                        folder.mkdir(parents=True, exist_ok=True)
                        writer = cv2.VideoWriter(str(folder/f'cam{receiver.cam}.mp4'),
                            cv2.VideoWriter_fourcc(*'mp4v'), 15., (w, h))
                        if not writer.isOpened():
                            raise RuntimeError('Cannot open MP4 writer')
                        timeline = (folder/f'cam{receiver.cam}.jsonl').open('w')
                    writer.write(frame)
                    timeline.write(json.dumps({'frame_id': fid, 'captured_at': captured})+'\n')
                    timeline.flush()
                with lock:
                    active = dict(target)
                opts = dict(conf=.25, imgsz=args.imgsz, device=args.device, verbose=False)
                if receiver.cam == 1:
                    validate(calibration, w/h)
                    result = model.track(frame, persist=True,
                        tracker=str(ROOT/'fixed_camera/bytetrack_fixed.yaml'), **opts)[0]
                else:
                    result = model.predict(frame, **opts)[0]
                people = []
                if result.boxes is not None and result.keypoints is not None:
                    boxes = result.boxes.xyxyn.cpu().tolist()
                    kpts = result.keypoints.data.cpu().numpy().copy()
                    kpts[:, :, 0] /= w
                    kpts[:, :, 1] /= h
                    ids = result.boxes.id
                    ids = ids.int().cpu().tolist() if ids is not None else [None]*len(boxes)
                    people = [Person(tuple(b), k.tolist(), tid, confidence)
                        for b, k, tid, confidence in zip(boxes, kpts, ids, result.boxes.conf.cpu().tolist())]
                now = time.time()
                if receiver.cam == 1:
                    rows = matcher.observe(people, time.monotonic()) if now-captured <= .8 else matcher.unknown('STALE')
                    client.send('seat_obs', value={'session': session, 'frame_id': fid,
                        'captured_at': captured, 'processed_at': now, 'seats': rows})
                elif active['seat'] == 1 and captured >= active['since']:
                    nose = nose_target(people) if now-captured <= .3 else None
                    client.send('track_obs', seat=1, value={'gen': active['gen'], 'frame_id': fid,
                        'captured_at': captured, 'status': 'TRACKED' if nose else 'LOST',
                        **({'cx': nose[0], 'cy': nose[1]} if nose else {})})
                count += 1
                if now-started >= 2:
                    fps = count/(now-started)
                    client.set_status(**{f'cam{receiver.cam}_fps': fps})
                    log.info('cam%d analysis %.1f fps, age %.0f ms', receiver.cam, fps, (now-captured)*1000)
                    count, started = 0, now
        except Exception as exc:
            log.exception('cam%d stopped', receiver.cam)
            failures.put(exc)
            stop.set()
        finally:
            if writer is not None:
                writer.release()
            if timeline is not None:
                timeline.close()

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
        log.info('Seat 1 test: hub -> UDP 5021/5022. cam2 must contain ONE person. Ctrl+C stops.')
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
