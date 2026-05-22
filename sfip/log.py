"""Logging setup: stderr diagnostics + per-run log file."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .config import LOG_DIR

logger = logging.getLogger("selectel_floating_ip")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _UtcIsoFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()


def setup_logging(*, log_to_file: bool = False) -> None:
    if getattr(setup_logging, "_configured", False):
        return
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stderr_handler)

    if log_to_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"run-{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(_UtcIsoFormatter("%(asctime)s %(message)s"))
        logger.addHandler(file_handler)

    setup_logging._configured = True  # type: ignore[attr-defined]
