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


def registration(samples, aspect):
    if len(samples) < 8:
        raise ValueError('Need at least 8 valid samples')
    xs, ys, scales = zip(*samples)
    if math.hypot(statistics.pstdev(xs)*aspect, statistics.pstdev(ys)) > .025:
        raise ValueError('Student moved too much: retry at normal seated posture')
    scale = statistics.median(scales)
    if scale < .015:
        raise ValueError('Shoulders too small/overlapping: adjust camera or posture')
    return {'anchor': [statistics.median(xs), statistics.median(ys)],
            'scale': scale, 'radius': min(.18, max(.055, scale*.9))}
