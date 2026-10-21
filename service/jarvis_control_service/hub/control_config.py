"""
control 전용 설정과 경로 (상세 설계 6.4, 7장). control 만 읽는다.
- config/control.yaml : 좌석 각도·옆자리·우선순위·타이머 (이 서비스 폴더 안)
- db/hub.db           : SQLite DB 파일
- service/jarvis_DB/create.sql : 스키마 원본 (db.py 가 시작할 때 실행)

control.yaml 은 중첩 구조라 PyYAML 이 꼭 필요하다. 없으면 시작하지 않는다
(측정한 좌석 각도가 조용히 무시되지 않게). control.yaml 파일 자체가 없을 때만 CONTROL_DEFAULTS 를 쓴다.
모든 노드가 같이 쓰는 설정(common.yaml)은 최상위 common/config.py 에 있다.
"""

import copy
import logging
from pathlib import Path

log = logging.getLogger("config")

SERVICE_DIR = Path(__file__).resolve().parent.parent      # service/jarvis_control_service/
CONTROL_CONFIG = SERVICE_DIR / "config" / "control.yaml"
DB_PATH = SERVICE_DIR / "db" / "hub.db"
CREATE_SQL = SERVICE_DIR.parent / "jarvis_DB" / "create.sql"

# control.yaml 이 없을 때 쓰는 값 (테스트도 이 값을 쓴다)
CONTROL_DEFAULTS = {
    "seats": {
        1: {"pan": 60, "tilt": 85, "neighbors": [2, 3]},       # 2×2 (1 2 / 3 4), 가로·세로 옆자리
        2: {"pan": 80, "tilt": 85, "neighbors": [1, 4]},
        3: {"pan": 100, "tilt": 85, "neighbors": [1, 4]},
        4: {"pan": 120, "tilt": 85, "neighbors": [2, 3]},
    },
    "priority": {"fall": 1, "question": 2, "away": 3},
    "timers": {
        "fall_neighbor_s": 15,
        "fall_all_s": 30,
        "question_expire_s": 180,
        "away_alert_s": 60,
        "class_condition_s": 10,
    },
    "track": {"nudge_max_hz": 10, "nudge_gain": 8.0},
}


class ConfigError(RuntimeError):
    pass


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "seats":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_control_config(path: Path = CONTROL_CONFIG) -> dict:
    """control.yaml. 없는 키는 CONTROL_DEFAULTS 로 채운다."""
    if not path.exists():
        log.warning("%s 없음 - 기본값 사용", path)
        return copy.deepcopy(CONTROL_DEFAULTS)
    try:
        import yaml
    except ImportError:
        raise ConfigError(f"PyYAML 미설치 - {path} 를 읽을 수 없습니다. "
                          f"pip install -r requirements.txt (또는 pip install pyyaml)") from None
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = _merge(CONTROL_DEFAULTS, data)
    cfg["seats"] = {int(k): v for k, v in cfg["seats"].items()}
    return cfg
