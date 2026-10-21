"""좌석별 팬틸트 각도·HOME·허용 범위 (Data Structure v1 5장 presets.json).

좌석은 정수 1~4. 각도는 정수(°). 프리셋이 없는 좌석으로는 움직이지 않는다.
preset_save를 받으면 현재 각도로 덮어쓰고 파일에 바로 저장한다.

Data Structure 형식에 'control' 항목(P 제어 값)을 추가했다. 규격 문서에도 추가 요청 필요.
기본 위치: service/jarvis_control_service/config/presets.json (control.yaml과 같은 폴더).
저장소 밖(전달용 압축 폴더)에서는 pantilt 폴더 옆의 presets.json을 쓴다.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DEFAULT_PATHS = (
    _HERE.parents[1] / 'config' / 'presets.json',   # 저장소: hub/pantilt -> jarvis_control_service/config
    _HERE.parent / 'presets.json',                  # 압축 폴더: pantilt 옆
)


def default_path() -> Path:
    for path in DEFAULT_PATHS:
        if path.exists():
            return path
    raise FileNotFoundError('presets.json을 찾지 못함: ' + ', '.join(map(str, DEFAULT_PATHS)))

# 펌웨어 고정 한계 (camera_controller.ino의 FW_PAN_MIN 등과 같아야 한다).
# Pan 0~180: 실물 전 구간 구동 확인. Tilt 140 초과: Pan 서보 전선 간섭으로 떨림. Tilt 30: 운용상 하한.
FW_LIMITS = {'pan': (0, 180), 'tilt': (30, 140)}

DEFAULT_CONTROL = {
    'kp': [18, 12],                 # 축별 이득. 초기값 = 화각(°) x 0.3, 화각 실측 후 조정
    'direction': [1, -1],           # 대상이 오른쪽/아래일 때 각도를 늘리면 1, 줄이면 -1 (실물 장착 방향)
    'initial_settle_seconds': 2.0,  # 최초 HOME 출력 후 대기
}


class PresetStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else default_path()
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        lim = raw['limits']
        self.limits = {'pan': (lim['pan_min'], lim['pan_max']),
                       'tilt': (lim['tilt_min'], lim['tilt_max'])}
        for axis, (lo, hi) in self.limits.items():
            fw_lo, fw_hi = FW_LIMITS[axis]
            if not (type(lo) is int and type(hi) is int and fw_lo <= lo <= hi <= fw_hi):
                raise ValueError(f'{axis} 범위 {lo}~{hi}가 펌웨어 고정 한계 {fw_lo}~{fw_hi}를 벗어남')
        self.home = self._pair(raw['home'], 'home')
        self.seats: dict[int, tuple[int, int]] = {}
        for key, value in raw.get('seats', {}).items():
            seat = int(key)
            if not 1 <= seat <= 255:
                raise ValueError(f'좌석 번호 오류: {key}')
            self.seats[seat] = self._pair(value, f'seat {key}')
        self.control = {**DEFAULT_CONTROL, **raw.get('control', {})}
        self.updated_at = float(raw.get('updated_at', 0))

    def _pair(self, value: dict, label: str) -> tuple[int, int]:
        pan, tilt = value['pan'], value['tilt']
        if type(pan) is not int or type(tilt) is not int:
            raise ValueError(f'{label}: 각도는 정수여야 합니다')
        if (pan, tilt) != self.clamp(pan, tilt):
            raise ValueError(f'{label}: 허용 범위 밖 각도 ({pan}, {tilt})')
        return pan, tilt

    def clamp(self, pan: int, tilt: int) -> tuple[int, int]:
        (p0, p1), (t0, t1) = self.limits['pan'], self.limits['tilt']
        return max(p0, min(p1, int(pan))), max(t0, min(t1, int(tilt)))

    def seat(self, seat: int) -> tuple[int, int] | None:
        """프리셋이 없으면 None."""
        return self.seats.get(int(seat))

    def max_span(self) -> int:
        return max(hi - lo for lo, hi in self.limits.values())

    def save(self, seat: int, pan: int, tilt: int, now: float | None = None) -> tuple[int, int]:
        """좌석 각도를 저장하고 파일에 바로 쓴다. 저장한 (pan, tilt)를 돌려준다."""
        seat = int(seat)
        if not 1 <= seat <= 255:
            raise ValueError(f'좌석 번호 오류: {seat}')
        angles = self.clamp(pan, tilt)
        self.seats[seat] = angles
        self.updated_at = time.time() if now is None else now
        self._write()
        return angles

    def _write(self) -> None:
        data = {
            'limits': {'pan_min': self.limits['pan'][0], 'pan_max': self.limits['pan'][1],
                       'tilt_min': self.limits['tilt'][0], 'tilt_max': self.limits['tilt'][1]},
            'home': {'pan': self.home[0], 'tilt': self.home[1]},
            'seats': {str(k): {'pan': v[0], 'tilt': v[1]} for k, v in sorted(self.seats.items())},
            'control': self.control,
            'updated_at': self.updated_at,
        }
        # 쓰는 도중 꺼져도 기존 파일이 깨지지 않게 임시 파일에 쓴 뒤 교체한다.
        tmp = self.path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(tmp, self.path)
