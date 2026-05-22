#!/usr/bin/env python3
"""Selectel floating IP helper — entry point and compatibility exports."""

from __future__ import annotations

import sys

import sfip.config as _config
from sfip.api import (
    ApiError,
    is_empty_request_error,
    is_project_locked_error,
    is_quota_exceeded_error,
    is_rate_limit_error,
    is_resource_not_found_error,
    is_transient_http_error,
    project_floating_ips,
)
from sfip.cli import attempts_label, main
from sfip.config import env_flag, env_float, env_int, normalize_chat_id
from sfip.matchers import address_matches_local_lists, filter_ips, load_local_matchers

ENV_PATH = _config.ENV_PATH

__all__ = [
    "ApiError",
    "ENV_PATH",
    "address_matches_local_lists",
    "attempts_label",
    "env_flag",
    "env_float",
    "env_int",
    "filter_ips",
    "is_empty_request_error",
    "is_project_locked_error",
    "is_quota_exceeded_error",
    "is_rate_limit_error",
    "is_resource_not_found_error",
    "is_transient_http_error",
    "load_env_file",
    "load_local_matchers",
    "main",
    "normalize_chat_id",
    "project_floating_ips",
]


def load_env_file() -> None:
    _config.ENV_PATH = ENV_PATH
    _config.load_env_file()


if __name__ == "__main__":
    sys.exit(main())
