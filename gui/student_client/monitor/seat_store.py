"""좌석별 기준값 저장소: 바른 자세 기준 7개 + 방향 측정값 8개를 좌석 단위로 저장·불러오기

전제: 카메라와 책상 위치는 바뀌지 않고, 사람만 자리를 옮긴다 → 좌석당 1세트(마지막 등록 값)
지금은 JSON 파일(data/seats/seat_<번호>.json, git 제외)로 저장한다.
DB로 바꿀 때는 같은 함수(load, save_baseline, save_zones)를 가진 클래스를 만들고 open_store에서 고르면 된다.
파일 형식 (DB 테이블 1행과 같은 구성):
  {"seat": 1, "updated_at": "...",
   "baseline": {"ear_shoulder", "head_ratio", "shoulder_y", "shoulder_tilt", "ear_tilt", "yaw", "pitch"},
   "zones": {"screen": {"yaw", "pitch"}, "edge_a": {...}, "edge_b": {...}, "desk": {...}}, "warnings": [...]}
"""
import json
from datetime import datetime
from pathlib import Path

from seat_common.posture_features import FEATURE_NAMES

BASELINE_KEYS = FEATURE_NAMES + ("yaw", "pitch")      # 바른 자세 기준 7개
ZONE_KEYS = ("screen", "edge_a", "edge_b", "desk")     # 방향 측정 4곳 (edge_a·b는 강의 공간이 없으면 생략)
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data" / "seats"


def valid_baseline(b):
    return isinstance(b, dict) and all(k in b for k in BASELINE_KEYS)


def valid_zones(z):
    return isinstance(z, dict) and "screen" in z and all("yaw" in v and "pitch" in v for v in z.values())


class JsonSeatStore:
    def __init__(self, root=DEFAULT_DIR):
        self.root = Path(root)

    def path(self, seat):
        return self.root / f"seat_{seat}.json"

    def _read(self, seat):
        p = self.path(seat)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"좌석 저장값을 읽지 못했습니다 ({p}): {e} → 다시 등록합니다")
            return {}

    def _write(self, seat, data):
        data.update(seat=seat, updated_at=datetime.now().isoformat(timespec="seconds"))
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path(seat).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path(seat))                  # 저장 중 끊겨도 기존 파일이 깨지지 않게

    def load(self, seat):
        """→ (바른 자세 기준 dict 또는 None, 방향 측정값 dict 또는 None)"""
        data = self._read(seat)
        b, z = data.get("baseline"), data.get("zones")
        return (b if valid_baseline(b) else None), (z if valid_zones(z) else None)

    def save_baseline(self, seat, baseline):
        data = self._read(seat)
        data["baseline"] = {k: round(float(baseline[k]), 4) for k in BASELINE_KEYS}
        self._write(seat, data)

    def save_zones(self, seat, zones, warnings=()):
        data = self._read(seat)
        data["zones"] = {k: {"yaw": float(v["yaw"]), "pitch": float(v["pitch"])} for k, v in zones.items()}
        data["warnings"] = list(warnings)
        self._write(seat, data)


def open_store(kind="json"):
    """저장 방식 선택. DB를 붙일 때 여기에 분기를 추가한다"""
    if kind == "json":
        return JsonSeatStore()
    raise ValueError(f"지원하지 않는 저장 방식: {kind}")