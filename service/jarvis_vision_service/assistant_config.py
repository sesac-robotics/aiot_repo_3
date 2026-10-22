"""
AI 조교(LLM) 설정 읽기: config/assistant.yaml + .env (GEMINI_API_KEY).
vision_service 전용. 공통 설정(config/common.yaml)과 섞지 않는다.

    from assistant_config import load_assistant_config
    cfg = load_assistant_config()      # dict. cfg["gemini_api_key"] 는 없으면 ""

python-dotenv 없이 .env 를 직접 읽는다 ("KEY=값" 한 줄씩, # 주석 무시).
이미 환경 변수에 있으면 그 값이 이긴다 (export GEMINI_API_KEY=... 로 덮어쓰기 가능).
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                 # service/jarvis_vision_service/
sys.path.insert(0, str(HERE.parents[1]))               # aiot_repo_3/ (common/)

from common.config import read_file                   # noqa: E402

ASSISTANT_CONFIG = HERE / "config" / "assistant.yaml"
ENV_FILE = HERE / ".env"

DEFAULTS = {
    "gemini_enabled": True,
    "gemini_model": "gemini-3.5-flash-lite",
    "gemini_timeout_s": 5,
    "gemini_max_tokens": 1024,
    "gemini_cooldown_s": 60,
    "gemini_thinking_level": "",
    "ollama_enabled": True,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:3b",
    "ollama_timeout_s": 10,
    "ollama_keep_alive": "30m",
    "ollama_warmup": True,
    "temperature": 0.3,
    "max_chars": 120,
    "max_concurrent": 2,
    # [질의]
    "gemini_ask_model": "gemini-3.5-flash-lite",
    "ask_timeout_s": 30,
    "ask_max_tool_rounds": 5,
    "gemini_ask_timeout_s": 15,
    "ollama_ask_timeout_s": 20,
    "ask_ollama_model": "",
    "ask_tool_timeout_s": 5,
    "ask_max_chars": 500,
    "ask_max_concurrent": 1,
    # [리포트]
    "report_timeout_s": 40,
    "ollama_report_timeout_s": 30,
}


def read_env_file(path: Path = ENV_FILE) -> dict:
    """.env 를 dict 로. 따옴표로 감싼 값은 따옴표를 벗긴다."""
    data = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


def load_assistant_config() -> dict:
    cfg = dict(DEFAULTS)
    if ASSISTANT_CONFIG.exists():
        cfg.update({k: v for k, v in read_file(ASSISTANT_CONFIG).items() if v is not None})
    env = read_env_file()
    cfg["gemini_api_key"] = os.environ.get("GEMINI_API_KEY") or env.get("GEMINI_API_KEY", "")
    return cfg


if __name__ == "__main__":
    # 설정 확인용:  python3 assistant_config.py   (키 값은 보여주지 않는다)
    c = load_assistant_config()
    key = c.pop("gemini_api_key")
    for k, v in c.items():
        print(f"{k:22} {v!r}")
    print(f"{'gemini_api_key':22} {'있음 (' + str(len(key)) + '자)' if key else '없음 → Gemini 건너뜀'}")
