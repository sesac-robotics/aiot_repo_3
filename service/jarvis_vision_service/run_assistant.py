"""Vision node: hub UDP frames -> pose observations / track_obs over TCP v3.

Calibration uses run_fixed_vision.py before starting the streamers.
Seats: every seat registered in fixed_seats.local.json (--only-seat N for a one-seat test).
cam2 tracking refuses multi-person frames (no cross-camera identity yet).
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
from fixed_camera.config import load
from fixed_camera.core import Person, SeatMatcher, valid_point
from fixed_camera.seat_report import SeatReport

log = logging.getLogger('vision')
ROOT = Path(__file__).resolve().parent
WARN_S = 10.0           # 같은 경고(버린 프레임, 처리 오류, 영상 비율)를 다시 찍기까지 최소 간격
MIN_SEAT_FPS = 2.0      # Control 판정은 관측 간격 0.8초(약 1.25 fps)를 넘으면 처음부터 다시 센다 → 여유를 두고 경고


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
        self.stale, self.worst_age, self.warned_at = 0, 0., 0.   # 시계 차이로 버린 프레임 (경고용)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def _drop_stale(self, age):
        """카메라 PC 와 이 PC 의 시계가 어긋나면 모든 프레임이 버려진다. 조용히 버리지 않고 알린다."""
        self.stale += 1
        self.worst_age = max(self.worst_age, abs(age))
        now = time.time()
        if now - self.warned_at >= WARN_S:
            log.warning('cam%d 프레임 %d개 버림: 촬영 시각 차이 최대 %.2f초 (허용 -0.2~0.8초). '
                        '카메라 PC 와 Vision PC 의 시계를 맞추세요 (NTP)', self.cam, self.stale, self.worst_age)
            self.stale, self.worst_age, self.warned_at = 0, 0., now

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
            age = time.time()-captured
            if not -.2 <= age <= .8:
                self._drop_stale(age)
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
            ap.error(f'Register seat {key} with run_fixed_vision.py --calibrate first')
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
        cam = receiver.cam
        rec = {'writer': None, 'timeline': None, 'on': bool(args.record_dir)}
        kpt_log = open(args.record_keypoints, 'a', encoding='utf-8') if (args.record_keypoints and cam == 1) else None
        matcher = SeatMatcher(calibration)
        report = SeatReport()                     # cam1: 좌석별 판정 요약 (10초마다 로그)
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
                          'run_fixed_vision.py --calibrate 로 좌석을 다시 등록하세요 (그동안 1번 좌석은 모름)',
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
                line = report.add(rows, now)
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
                log.info('cam%d analysis %.1f fps, age %.0f ms', cam, fps, (now-captured)*1000)
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
