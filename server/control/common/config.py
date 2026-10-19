"""
설정 읽기
- config/common.yaml  : control IP·포트. 관제 노트북 IP는 여기서만 바꾼다. (모든 노드)
- config/control.yaml : 좌석 각도·옆자리·우선순위·타이머 (control 만)

PyYAML 이 있으면 그걸 쓰고, 없으면 common.yaml 은 "키: 값" 한 줄짜리만 읽는 간단한 파서로 읽는다.
control.yaml 은 중첩 구조라 PyYAML 이 없으면 아래 CONTROL_DEFAULTS 를 쓴다.
명령줄 옵션(--server, --port)을 load_config(...) 에 넘기면 그게 이긴다.

옛 키 hub_host / hub_port 도 계속 읽는다. load_config() 결과에는 두 이름이 모두 들어 있다.
"""

import ast
import copy
import logging
from pathlib import Path

from common.protocol import DEFAULT_PORT

log = logging.getLogger("config")

ROOT = Path(__file__).resolve().parent.parent        # server/control/
CONFIG_DIR = ROOT / "config"
COMMON_CONFIG = CONFIG_DIR / "common.yaml"
CONTROL_CONFIG = CONFIG_DIR / "control.yaml"
DB_PATH = ROOT / "hub.db"                             # 어디서 실행해도 server/control/hub.db
CREATE_SQL = ROOT / "db" / "create.sql"               # 스키마 원본 (db.py 가 시작할 때 실행)

DEFAULTS = {"control_host": "127.0.0.1", "control_port": DEFAULT_PORT, "log_level": "INFO"}

# control.yaml 이 없거나 PyYAML 이 없을 때 쓰는 값 (상세 설계 6.4)
CONTROL_DEFAULTS = {
    "seats": {
        1: {"pan": 60, "tilt": 85, "neighbors": [2]},
        2: {"pan": 80, "tilt": 85, "neighbors": [1, 3]},
        3: {"pan": 100, "tilt": 85, "neighbors": [2, 4]},
        4: {"pan": 120, "tilt": 85, "neighbors": [3]},
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


def _parse_scalar(text: str):
    text = text.strip()
    if text == "":
        return None
    low = text.lower()
    if low in ("null", "none", "~"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return ast.literal_eval(text)          # 숫자, "문자열", [1, 2] 등
    except (ValueError, SyntaxError):
        return text                            # 따옴표 없는 문자열


def _read_simple(path: Path) -> dict:
    data = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line or line.startswith((" ", "\t")):
            raise ValueError(f"{path}:{lineno} 'key: value' 형식만 쓸 수 있음 (PyYAML 미설치)")
        key, value = line.split(":", 1)
        data[key.strip()] = _parse_scalar(value)
    return data


def read_file(path: Path) -> dict:
    try:
        import yaml
    except ImportError:
        return _read_simple(path)
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(**overrides) -> dict:
    """common.yaml. 키 이름은 control_host / control_port (옛 hub_host / hub_port 도 읽음)."""
    cfg = dict(DEFAULTS)
    if COMMON_CONFIG.exists():
        file_cfg = read_file(COMMON_CONFIG)
        for old, new in (("hub_host", "control_host"), ("hub_port", "control_port")):
            if old in file_cfg and new not in file_cfg:
                file_cfg[new] = file_cfg[old]
        cfg.update(file_cfg)
    # 옛 이름으로 넘긴 옵션도 받는다
    for old, new in (("hub_host", "control_host"), ("hub_port", "control_port")):
        if overrides.get(old) is not None and overrides.get(new) is None:
            overrides[new] = overrides[old]
        overrides.pop(old, None)
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    cfg["hub_host"], cfg["hub_port"] = cfg["control_host"], cfg["control_port"]   # 옛 코드용
    return cfg


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "seats":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_control_config(path: Path = CONTROL_CONFIG) -> dict:
    """control.yaml (좌석·우선순위·타이머·추적). 없는 키는 CONTROL_DEFAULTS 로 채운다."""
    if not path.exists():
        log.warning("%s 없음 - 기본값 사용", path)
        return copy.deepcopy(CONTROL_DEFAULTS)
    try:
        import yaml
    except ImportError:
        log.warning("PyYAML 미설치 - %s 대신 기본값 사용 (pip install pyyaml)", path)
        return copy.deepcopy(CONTROL_DEFAULTS)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = _merge(CONTROL_DEFAULTS, data)
    cfg["seats"] = {int(k): v for k, v in cfg["seats"].items()}
    return cfg
