"""IP/CIDR allow-list loading and matching."""

from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path

from .config import SCRIPT_DIR, env


def candidate_ip_dirs() -> list[Path]:
    return [
        SCRIPT_DIR / "ip",
        Path(r"F:\yandex-cloud-ip\ip"),
        Path(r"F:\reg-cloudvps-ip\ip"),
    ]


def default_ip_list_dir() -> Path:
    explicit = env("SELECTEL_IP_LIST_DIR", required=False)
    if explicit:
        return Path(str(explicit))
    for path in candidate_ip_dirs():
        if path.exists():
            return path
    return candidate_ip_dirs()[0]


def load_local_matchers(directory_path: Path) -> tuple[set[str], list[ipaddress._BaseNetwork]]:
    ip_set: set[str] = set()
    networks: list[ipaddress._BaseNetwork] = []
    if not directory_path.exists() or not directory_path.is_dir():
        return ip_set, networks
    for file_path in sorted(directory_path.glob("*.txt")):
        for raw_line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                if "/" in line:
                    networks.append(ipaddress.ip_network(line, strict=False))
                else:
                    ip_set.add(str(ipaddress.ip_address(line)))
            except ValueError:
                continue
    return ip_set, networks


def address_matches_local_lists(
    address_value: str,
    ip_set: set[str],
    networks: list[ipaddress._BaseNetwork],
) -> bool:
    if not address_value:
        return False
    try:
        target = ipaddress.ip_address(address_value)
    except ValueError:
        return False
    if str(target) in ip_set:
        return True
    return any(target in network for network in networks)


def filter_ips(ips: list[dict], args: argparse.Namespace) -> list[dict]:
    items = ips
    if getattr(args, "project_id", None):
        items = [item for item in items if item.get("project_id") == args.project_id]
    if getattr(args, "ip", None):
        items = [item for item in items if item.get("floating_ip_address") == args.ip]
    if getattr(args, "prefix", None):
        items = [item for item in items if str(item.get("floating_ip_address", "")).startswith(args.prefix)]
    if getattr(args, "status", None):
        items = [item for item in items if item.get("status") == args.status]
    if getattr(args, "local_list", False):
        directory_path = Path(args.ip_list_dir)
        ip_set, networks = load_local_matchers(directory_path)
        items = [
            item
            for item in items
            if address_matches_local_lists(str(item.get("floating_ip_address", "")), ip_set, networks)
        ]
    return items
