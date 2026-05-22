from __future__ import annotations

import json

from selectel_floating_ip import (
    ApiError,
    is_empty_request_error,
    is_project_locked_error,
    is_quota_exceeded_error,
    is_rate_limit_error,
    is_resource_not_found_error,
    is_transient_http_error,
)


def err(status: int | None, details: str | dict = "") -> ApiError:
    body = json.dumps(details) if isinstance(details, dict) else details
    return ApiError("test", status_code=status, details=body)


def test_quota_exceeded_json_payload():
    assert is_quota_exceeded_error(err(409, {"error": "quota_exceeded"}))


def test_quota_exceeded_plain_text():
    assert is_quota_exceeded_error(err(409, "quota_exceeded for floatingips"))


def test_quota_exceeded_wrong_status():
    assert not is_quota_exceeded_error(err(400, {"error": "quota_exceeded"}))


def test_quota_exceeded_other_error():
    assert not is_quota_exceeded_error(err(409, {"error": "something_else"}))


def test_project_locked():
    assert is_project_locked_error(err(400, {"error": "project_is_locked"}))
    assert is_project_locked_error(err(400, "project_is_locked details"))
    assert not is_project_locked_error(err(403, {"error": "project_is_locked"}))


def test_empty_request_error_only_when_status_is_none():
    assert is_empty_request_error(err(None, "network error"))
    assert not is_empty_request_error(err(500, "server error"))


def test_rate_limit():
    assert is_rate_limit_error(err(429, {"error": "too_many_requests"}))
    assert is_rate_limit_error(err(429, {"error": "rate_limit_exceeded"}))
    assert is_rate_limit_error(err(429, "rate limit exceeded"))
    assert is_rate_limit_error(err(429, "too_many_requests"))
    assert not is_rate_limit_error(err(500, {"error": "too_many_requests"}))


def test_transient_http_error():
    for code in (408, 500, 502, 503, 504):
        assert is_transient_http_error(err(code))
    assert not is_transient_http_error(err(400))
    assert not is_transient_http_error(err(404))
    assert not is_transient_http_error(err(None))


def test_resource_not_found():
    assert is_resource_not_found_error(err(404, {"error": "resource_not_found"}))
    assert is_resource_not_found_error(err(404, {"error": "resource_quota_not_found"}))
    assert is_resource_not_found_error(err(404, "resource_not_found here"))
    assert not is_resource_not_found_error(err(404, {"error": "something_else"}))
    assert not is_resource_not_found_error(err(500))


def test_api_error_retry_after_defaults_to_none():
    error = ApiError("x", status_code=429, details="rate")
    assert error.retry_after is None


def test_api_error_carries_retry_after():
    error = ApiError("x", status_code=429, details="rate", retry_after=420.0)
    assert error.retry_after == 420.0
