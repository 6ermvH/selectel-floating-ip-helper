from __future__ import annotations

from types import SimpleNamespace

from selectel_floating_ip import (
    ApiError,
    attempts_label,
    filter_ips,
    project_floating_ips,
)
from sfip.cli import _format_duration, _rate_limit_backoff


def test_attempts_label_unlimited():
    assert attempts_label(0) == "unlimited"
    assert attempts_label(-1) == "unlimited"


def test_attempts_label_finite():
    assert attempts_label(5) == "5"


def test_format_duration_seconds():
    assert _format_duration(0) == "0s"
    assert _format_duration(45) == "45s"


def test_format_duration_minutes():
    assert _format_duration(60) == "1m00s"
    assert _format_duration(125) == "2m05s"


def test_format_duration_hours():
    assert _format_duration(3600) == "1h00m"
    assert _format_duration(60000) == "16h40m"


def test_rate_limit_backoff_uses_retry_after_as_lower_bound(monkeypatch):
    monkeypatch.setenv("SELECTEL_RATE_LIMIT_BACKOFF_MIN_SECONDS", "100")
    monkeypatch.setenv("SELECTEL_RATE_LIMIT_BACKOFF_MAX_SECONDS", "200")
    # Retry-After bigger than the env floor — the floor must lift to it
    error = ApiError("x", status_code=429, retry_after=500.0)
    for _ in range(20):
        backoff = _rate_limit_backoff(error)
        assert backoff >= 500.0


def test_rate_limit_backoff_falls_within_env_window(monkeypatch):
    monkeypatch.setenv("SELECTEL_RATE_LIMIT_BACKOFF_MIN_SECONDS", "540")
    monkeypatch.setenv("SELECTEL_RATE_LIMIT_BACKOFF_MAX_SECONDS", "720")
    error = ApiError("x", status_code=429)  # no Retry-After
    for _ in range(20):
        backoff = _rate_limit_backoff(error)
        assert 540.0 <= backoff <= 720.0


def make_ip(**fields):
    base = {
        "floating_ip_address": "203.0.113.1",
        "project_id": "proj-a",
        "status": "DOWN",
    }
    base.update(fields)
    return base


def test_project_floating_ips_filters_by_project():
    ips = [
        make_ip(project_id="proj-a", floating_ip_address="203.0.113.1"),
        make_ip(project_id="proj-b", floating_ip_address="203.0.113.2"),
        make_ip(project_id="proj-a", floating_ip_address="203.0.113.3"),
    ]
    result = project_floating_ips(ips, "proj-a")
    assert {item["floating_ip_address"] for item in result} == {"203.0.113.1", "203.0.113.3"}


def args(**kw):
    defaults = dict(
        project_id=None,
        ip=None,
        prefix=None,
        status=None,
        local_list=False,
        ip_list_dir=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_filter_ips_by_project_id():
    ips = [make_ip(project_id="a"), make_ip(project_id="b")]
    out = filter_ips(ips, args(project_id="a"))
    assert len(out) == 1 and out[0]["project_id"] == "a"


def test_filter_ips_by_exact_ip():
    ips = [make_ip(floating_ip_address="1.1.1.1"), make_ip(floating_ip_address="2.2.2.2")]
    out = filter_ips(ips, args(ip="2.2.2.2"))
    assert len(out) == 1 and out[0]["floating_ip_address"] == "2.2.2.2"


def test_filter_ips_by_prefix():
    ips = [make_ip(floating_ip_address="203.0.113.1"), make_ip(floating_ip_address="198.51.100.1")]
    out = filter_ips(ips, args(prefix="203."))
    assert len(out) == 1 and out[0]["floating_ip_address"].startswith("203.")


def test_filter_ips_by_status():
    ips = [make_ip(status="ACTIVE"), make_ip(status="DOWN")]
    out = filter_ips(ips, args(status="ACTIVE"))
    assert len(out) == 1 and out[0]["status"] == "ACTIVE"


def test_filter_ips_combined_filters():
    ips = [
        make_ip(project_id="a", status="ACTIVE", floating_ip_address="1.1.1.1"),
        make_ip(project_id="a", status="DOWN", floating_ip_address="1.1.1.2"),
        make_ip(project_id="b", status="ACTIVE", floating_ip_address="1.1.1.3"),
    ]
    out = filter_ips(ips, args(project_id="a", status="ACTIVE"))
    assert len(out) == 1 and out[0]["floating_ip_address"] == "1.1.1.1"


def test_filter_ips_by_local_list(tmp_path):
    list_file = tmp_path / "allow.txt"
    list_file.write_text("203.0.113.0/24\n", encoding="utf-8")
    ips = [
        make_ip(floating_ip_address="203.0.113.5"),
        make_ip(floating_ip_address="198.51.100.5"),
    ]
    out = filter_ips(ips, args(local_list=True, ip_list_dir=str(tmp_path)))
    assert len(out) == 1 and out[0]["floating_ip_address"] == "203.0.113.5"
