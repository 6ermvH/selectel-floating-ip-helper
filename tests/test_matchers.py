from __future__ import annotations

import ipaddress
from pathlib import Path

from selectel_floating_ip import (
    address_matches_local_lists,
    load_local_matchers,
)


def test_address_matches_exact_ip():
    ip_set = {"203.0.113.5"}
    assert address_matches_local_lists("203.0.113.5", ip_set, [])
    assert not address_matches_local_lists("203.0.113.6", ip_set, [])


def test_address_matches_cidr():
    networks = [ipaddress.ip_network("203.0.113.0/24")]
    assert address_matches_local_lists("203.0.113.42", set(), networks)
    assert not address_matches_local_lists("198.51.100.1", set(), networks)


def test_address_matches_ipv6_cidr():
    networks = [ipaddress.ip_network("2001:db8::/32")]
    assert address_matches_local_lists("2001:db8::1", set(), networks)
    assert not address_matches_local_lists("2001:dead::1", set(), networks)


def test_address_empty_returns_false():
    assert not address_matches_local_lists("", {"203.0.113.5"}, [])


def test_address_invalid_returns_false():
    assert not address_matches_local_lists("not-an-ip", {"203.0.113.5"}, [])


def test_load_local_matchers_parses_ips_cidrs_comments(tmp_path: Path):
    (tmp_path / "a.txt").write_text(
        "# comment\n"
        "203.0.113.5\n"
        "\n"
        "203.0.113.0/24\n"
        "  198.51.100.10  \n",
        encoding="utf-8",
    )
    (tmp_path / "b.txt").write_text("# only comment\n", encoding="utf-8")
    ip_set, networks = load_local_matchers(tmp_path)
    assert ip_set == {"203.0.113.5", "198.51.100.10"}
    assert ipaddress.ip_network("203.0.113.0/24") in networks


def test_load_local_matchers_skips_malformed(tmp_path: Path):
    (tmp_path / "x.txt").write_text(
        "203.0.113.5\nnot-an-ip\n999.999.999.999\n",
        encoding="utf-8",
    )
    ip_set, networks = load_local_matchers(tmp_path)
    assert ip_set == {"203.0.113.5"}
    assert networks == []


def test_load_local_matchers_missing_dir_returns_empty(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    ip_set, networks = load_local_matchers(missing)
    assert ip_set == set()
    assert networks == []


def test_load_local_matchers_ignores_non_txt_files(tmp_path: Path):
    (tmp_path / "data.txt").write_text("203.0.113.5\n", encoding="utf-8")
    (tmp_path / "data.csv").write_text("198.51.100.1\n", encoding="utf-8")
    ip_set, _ = load_local_matchers(tmp_path)
    assert ip_set == {"203.0.113.5"}
