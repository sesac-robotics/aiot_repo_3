"""Explicit registration with atomic persistence, no default classroom coordinates."""
import json
import math
import os
from pathlib import Path
import statistics


def validate(config, aspect=None):
    if config.get('version') != 1 or config.get('camera_id') != 1:
        raise ValueError('Use version=1, camera_id=1 fixed-camera calibration')
    if not math.isfinite(config.get('aspect', 0)) or config['aspect'] <= 0:
        raise ValueError('Invalid calibration aspect ratio')
    if aspect is not None and abs(config['aspect']-aspect) > .01:
        raise ValueError('Image aspect changed: recalibrate seats')
    seats = config.get('seats', {})
    if not seats or len(seats) > 4 or any(str(k) not in ('1','2','3','4') for k in seats):
        raise ValueError('Register seats 1..4 first')
    for item in seats.values():
        a = item['anchor']
        if (len(a) != 2 or not all(math.isfinite(v) and 0 < v < 1 for v in a)
                or not .015 <= item['scale'] <= .8
                or not .02 <= item['radius'] <= .4):
            raise ValueError('Invalid seat anchor/scale/radius')
    return config


def load(path):
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def save(path, config):
    validate(config)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(tmp, p)


class Enrollment:
    """좌석 하나 등록: 클릭한 학생의 어깨 표본을 모은다 (app.py 등록 화면이 프레임마다 add 를 부른다).

    예전에는 한 프레임이라도 어깨를 놓치면 바로 취소되고, 2초 안에 8개를 못 모으면(4 fps 미만) 실패했다.
    멀리 앉은 뒷줄 학생은 어깨 점이 잠깐씩 깜빡여서 등록이 거의 되지 않았다.
    - max_gap_s 이내로 잠깐 놓치는 것은 참는다 (그 동안의 표본은 그대로 둔다)
    - hold_s 가 지나고 min_samples 개가 모이면 끝. 느리면 timeout_s 까지 더 기다린다
    """
    def __init__(self, start, hold_s=2., min_samples=8, max_gap_s=.6, timeout_s=6.):
        self.start, self.last_ok = start, start
        self.hold_s, self.min_samples, self.max_gap_s, self.timeout_s = hold_s, min_samples, max_gap_s, timeout_s
        self.samples = []
        self.reason = ''

    def add(self, sample, now):
        """sample = (x, y, scale) 또는 None(이번 프레임에 어깨·추적을 놓침) → 'collecting' | 'done' | 'cancelled'"""
        if sample is None:
            if now - self.last_ok > self.max_gap_s:
                self.reason = f'shoulders/track lost for {now - self.last_ok:.1f}s'
                return 'cancelled'
        else:
            self.samples.append(sample)
            self.last_ok = now
        elapsed = now - self.start
        if elapsed >= self.hold_s and len(self.samples) >= self.min_samples:
            return 'done'
        if elapsed >= self.timeout_s:
            self.reason = (f'only {len(self.samples)}/{self.min_samples} samples in {self.timeout_s:.0f}s '
                           '(analysis too slow: lower --imgsz or use GPU)')
            return 'cancelled'
        return 'collecting'


def registration(samples, aspect):
    if len(samples) < 8:
        raise ValueError('Need at least 8 valid samples')
    xs, ys, scales = zip(*samples)
    spread = math.hypot(statistics.pstdev(xs)*aspect, statistics.pstdev(ys))
    if spread > .025:
        raise ValueError(f'Student moved too much ({spread:.3f} > 0.025): retry at normal seated posture')
    scale = statistics.median(scales)
    if scale < .015:
        raise ValueError('Shoulders too small/overlapping: adjust camera or posture')
    return {'anchor': [statistics.median(xs), statistics.median(ys)],
            'scale': scale, 'radius': min(.18, max(.055, scale*.9))}
