"""Environment + filesystem configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = SCRIPT_DIR / ".env"
LOG_DIR = SCRIPT_DIR / "logs"


def load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, *, required: bool = True, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def env_int(name: str, default: int) -> int:
    value = env(name, required=False, default=str(default))
    try:
        return int(str(value))
    except (TypeError, ValueError) as error:
        raise SystemExit(f"{name} must be an integer") from error


def env_float(name: str, default: float) -> float:
    value = env(name, required=False, default=str(default))
    try:
        return float(str(value))
    except (TypeError, ValueError) as error:
        raise SystemExit(f"{name} must be a number") from error


def env_flag(name: str, default: bool = False) -> bool:
    value = str(env(name, required=False, default="1" if default else "0") or "").strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


def normalize_chat_id(raw_chat_id: object) -> str:
    return str(raw_chat_id or "").strip()
