"""
설정 읽기: config/common.yaml (hub IP·포트). 관제 노트북 IP는 여기서만 바꾼다.

PyYAML 이 있으면 그걸 쓰고, 없으면 "키: 값" 한 줄짜리만 읽는 간단한 파서로 읽는다.
명령줄 옵션(--server, --port)을 load_config(...) 에 넘기면 그게 이긴다.
"""

import ast
from pathlib import Path

from common.message import DEFAULT_PORT

ROOT = Path(__file__).resolve().parent.parent        # src/
CONFIG_DIR = ROOT / "config"
COMMON_CONFIG = CONFIG_DIR / "common.yaml"
DB_PATH = ROOT / "hub.db"                             # 어디서 실행해도 src/hub.db

DEFAULTS = {"hub_host": "127.0.0.1", "hub_port": DEFAULT_PORT, "log_level": "INFO"}


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
    cfg = dict(DEFAULTS)
    if COMMON_CONFIG.exists():
        cfg.update(read_file(COMMON_CONFIG))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg
