"""
공통 설정 읽기: aiot_repo_3/config/common.yaml (control IP·포트). 모든 노드가 같이 쓴다.
관제 노트북 IP는 여기서만 바꾼다.

PyYAML 이 있으면 그걸 쓰고, 없으면 "키: 값" 한 줄짜리만 읽는 간단한 파서로 읽는다.
명령줄 옵션(--server, --port)을 load_config(...) 에 넘기면 그게 이긴다.
옛 키 hub_host / hub_port 도 계속 읽는다. load_config() 결과에는 두 이름이 모두 들어 있다.

control 전용 설정(control.yaml, DB 경로)은 service/jarvis_control_service/hub/control_config.py 에 있다.
"""

import ast
from pathlib import Path

from common.protocol import DEFAULT_PORT

REPO_ROOT = Path(__file__).resolve().parent.parent    # aiot_repo_3/
CONFIG_DIR = REPO_ROOT / "config"
COMMON_CONFIG = CONFIG_DIR / "common.yaml"

DEFAULTS = {"control_host": "127.0.0.1", "control_port": DEFAULT_PORT, "log_level": "INFO"}


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
