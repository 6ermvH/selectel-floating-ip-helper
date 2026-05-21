"""Run-log file helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import LOG_DIR


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"run-{datetime.now().strftime('%Y%m%d')}.log"


def append_log_line(log_path: Path | None, message: str) -> None:
    if not log_path:
        return
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{utc_now()} {message}\n")
