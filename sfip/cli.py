"""Argparse entry point and command handlers."""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

from .api import (
    ApiError,
    batch_size_backoff,
    cleanup_created_ip,
    cleanup_nonmatching_project_ips,
    create_floating_ips,
    delete_floating_ip,
    find_existing_matching_ip,
    is_empty_request_error,
    is_project_locked_error,
    is_quota_exceeded_error,
    is_rate_limit_error,
    is_resource_not_found_error,
    is_transient_http_error,
    list_floating_ips,
    list_projects,
    planned_batch_size,
    sleep_with_jitter,
)
from .config import SCRIPT_DIR, env, env_float, env_int, load_env_file
from .log import append_log_line, init_log_path, utc_now
from .matchers import (
    address_matches_local_lists,
    default_ip_list_dir,
    filter_ips,
    load_local_matchers,
)
from .telegram import (
    confirm_continue_on_existing_match,
    notify_success,
    resolve_match_action,
    telegram_confirmation_enabled,
)


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def attempts_label(max_attempts: int) -> str:
    return "unlimited" if max_attempts <= 0 else str(max_attempts)


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def output_mode(args: argparse.Namespace) -> str:
    if getattr(args, "json_output", False):
        return "json"
    return str(env("SELECTEL_OUTPUT_MODE", required=False, default="compact") or "compact").strip().lower()


def emit(args: argparse.Namespace, payload: dict, compact_line: str | None = None) -> None:
    if output_mode(args) == "json" or not compact_line:
        print_json(payload)
        log_path = getattr(args, "log_path", None)
        append_log_line(log_path, json.dumps(payload, ensure_ascii=False))
        return
    print(compact_line)
    append_log_line(getattr(args, "log_path", None), compact_line)


def write_pending_match(args: argparse.Namespace, payload: dict) -> None:
    pending_path = SCRIPT_DIR / "pending_match.json"
    pending_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(
        args,
        {"pending_match": True, "path": str(pending_path), **payload},
        compact_line=(
            f"pending match saved path={pending_path} "
            f"ip={payload.get('ip')} id={payload.get('id')} reason={payload.get('reason')}"
        ),
    )


def cmd_auth_check(token: str, _: argparse.Namespace) -> int:
    projects = list_projects(token)
    print_json({"ok": True, "projects": projects})
    return 0


def cmd_list(token: str, args: argparse.Namespace) -> int:
    items = list_floating_ips(token)
    items = filter_ips(items, args)
    print_json({"floatingips": items})
    return 0


def cmd_find(token: str, args: argparse.Namespace) -> int:
    items = list_floating_ips(token)
    items = filter_ips(items, args)
    emit(
        args,
        {"matches": items, "count": len(items), "ip_list_dir": args.ip_list_dir if args.local_list else None},
        compact_line=f"matches={len(items)} ip_list_dir={args.ip_list_dir}" if args.local_list else f"matches={len(items)}",
    )
    return 0 if items else 1


def cmd_create(token: str, args: argparse.Namespace) -> int:
    args.log_path = init_log_path()
    project_id = args.project_id or str(env("SELECTEL_PROJECT_ID"))
    region = args.region or str(env("SELECTEL_REGION"))
    list_dir = Path(args.ip_list_dir)
    ip_set, networks = load_local_matchers(list_dir)
    emit(
        args,
        {
            "started": True,
            "project_id": project_id,
            "region": region,
            "max_attempts": args.max_attempts,
            "ip_list_dir": str(list_dir),
            "entries_loaded": len(ip_set) + len(networks),
        },
        compact_line=(
            f"start project={project_id} region={region} "
            f"max_attempts={attempts_label(args.max_attempts)} "
            f"ip_list_dir={list_dir} entries={len(ip_set) + len(networks)}"
        ),
    )
    if not ip_set and not networks:
        raise SystemExit(f"IP list directory is empty or missing: {list_dir}")

    if args.dry_run:
        batch_size, _ = planned_batch_size(token, project_id)
        emit(
            args,
            {
                "dry_run": True,
                "project_id": project_id,
                "region": region,
                "max_attempts": args.max_attempts,
                "batch_size": batch_size,
                "ip_list_dir": str(list_dir),
                "request": {"floatingips": [{"quantity": batch_size, "region": region}]},
            },
            compact_line=(
                f"dry-run project={project_id} region={region} "
                f"max_attempts={attempts_label(args.max_attempts)} batch_size={batch_size} ip_list_dir={list_dir}"
            ),
        )
        return 0

    existing_match = find_existing_matching_ip(token, project_id, ip_set, networks)
    if existing_match:
        existing_address = str(existing_match.get("floating_ip_address", ""))
        existing_id = str(existing_match.get("id", ""))
        emit(
            args,
            {"matched_existing": True, "ip_list_dir": str(list_dir), "ip": existing_match},
            compact_line=f"existing match ip={existing_address} id={existing_id}",
        )
        existing_message = (
            "Найден подходящий "
            "существующий floating IP.\n"
            f"IP: {existing_address}\n"
            f"ID: {existing_id}\n"
            f"Region: {existing_match.get('region') or '-'}\n"
            f"Project: {project_id}\n"
            f"Source list: {list_dir}"
        )
        if telegram_confirmation_enabled():
            decision = resolve_match_action(existing_message)
            if decision == "keep_continue":
                notify_success(existing_message + "\nDecision: keep and continue.")
                print("Continuing search after Telegram approval.")
            elif decision == "delete_continue":
                cleanup_created_ip(token, existing_id, existing_address)
                notify_success(existing_message + "\nDecision: delete and continue.")
            else:
                notify_success(existing_message + "\nDecision: keep and stop.")
                return 0
        elif not confirm_continue_on_existing_match(existing_address, existing_id):
            notify_success(existing_message)
            return 0
        else:
            print("Continuing search despite existing matching IP.")

    attempt = 0
    cached_ips: list[dict] = []
    while args.max_attempts <= 0 or attempt < args.max_attempts:
        attempt += 1
        created_items: list[dict] = []
        match_kept = False
        try:
            batch_size, cached_ips = planned_batch_size(token, project_id)
            while True:
                try:
                    created_items = create_floating_ips(token, project_id, region, quantity=batch_size)
                    break
                except ApiError as error:
                    if is_rate_limit_error(error):
                        backoff_sec = random.uniform(
                            env_float("SELECTEL_RATE_LIMIT_BACKOFF_MIN_SECONDS", 300.0),
                            env_float("SELECTEL_RATE_LIMIT_BACKOFF_MAX_SECONDS", 600.0),
                        )
                        emit(
                            args,
                            {
                                "rate_limited": True,
                                "status_code": error.status_code,
                                "attempt": attempt,
                                "sleep_seconds": round(backoff_sec, 1),
                                "details": error.details.strip() or "<empty>",
                            },
                            compact_line=(
                                f"attempt {attempt} -> rate limited "
                                f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                            ),
                        )
                        time.sleep(backoff_sec)
                        continue
                    if is_transient_http_error(error):
                        backoff_sec = random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        )
                        emit(
                            args,
                            {
                                "transient_error": True,
                                "status_code": error.status_code,
                                "attempt": attempt,
                                "sleep_seconds": round(backoff_sec, 1),
                            },
                            compact_line=(
                                f"attempt {attempt} -> HTTP {error.status_code} "
                                f"({error.details.strip() or 'transient error'}), retry after {backoff_sec:.0f}s"
                            ),
                        )
                        time.sleep(backoff_sec)
                        continue
                    if is_empty_request_error(error):
                        backoff_sec = random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        )
                        emit(
                            args,
                            {
                                "request_error": True,
                                "attempt": attempt,
                                "sleep_seconds": round(backoff_sec, 1),
                                "details": error.details.strip() or "<empty>",
                            },
                            compact_line=(
                                f"attempt {attempt} -> request error "
                                f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                            ),
                        )
                        time.sleep(backoff_sec)
                        continue
                    if is_resource_not_found_error(error):
                        backoff_sec = random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        )
                        emit(
                            args,
                            {
                                "resource_not_found": True,
                                "status_code": error.status_code,
                                "attempt": attempt,
                                "sleep_seconds": round(backoff_sec, 1),
                                "details": error.details.strip() or "<empty>",
                            },
                            compact_line=(
                                f"attempt {attempt} -> resource not found "
                                f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                            ),
                        )
                        time.sleep(backoff_sec)
                        continue
                    if is_project_locked_error(error):
                        backoff_sec = random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        )
                        emit(
                            args,
                            {
                                "project_locked": True,
                                "status_code": error.status_code,
                                "attempt": attempt,
                                "sleep_seconds": round(backoff_sec, 1),
                            },
                            compact_line=(
                                f"attempt {attempt} -> project locked "
                                f"({error.details.strip() or 'project_is_locked'}), retry after {backoff_sec:.0f}s"
                            ),
                        )
                        time.sleep(backoff_sec)
                        continue
                    if not is_quota_exceeded_error(error):
                        raise
                    _, cached_ips = planned_batch_size(token, project_id)
                    deleted_items = cleanup_nonmatching_project_ips(token, project_id, ip_set, networks, cached_ips)
                    if deleted_items:
                        emit(
                            args,
                            {
                                "quota_recovered": True,
                                "attempt": attempt,
                                "deleted_count": len(deleted_items),
                                "deleted": deleted_items,
                            },
                            compact_line=(
                                f"attempt {attempt} -> quota recovered by deleting "
                                f"{len(deleted_items)} non-matching floating IP(s)"
                            ),
                        )
                    else:
                        emit(
                            args,
                            {"quota_hit": True, "attempt": attempt},
                            compact_line=f"attempt {attempt} -> quota still full, waiting...",
                        )
                        time.sleep(random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        ))
                        continue
                    next_batch_size = batch_size_backoff(batch_size)
                    if next_batch_size == batch_size:
                        emit(
                            args,
                            {"quota_hit_stuck": True, "attempt": attempt, "batch_size": batch_size},
                            compact_line=f"attempt {attempt} -> quota stuck at batch {batch_size}, waiting...",
                        )
                        time.sleep(random.uniform(
                            env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                            env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                        ))
                        continue
                    emit(
                        args,
                        {
                            "batch_reduced": True,
                            "attempt": attempt,
                            "from_batch_size": batch_size,
                            "to_batch_size": next_batch_size,
                        },
                        compact_line=(
                            f"attempt {attempt} -> quota hit, reducing batch "
                            f"{batch_size} -> {next_batch_size}"
                        ),
                    )
                    batch_size = next_batch_size
            matching_items = [
                item
                for item in created_items
                if address_matches_local_lists(str(item.get("floating_ip_address", "")), ip_set, networks)
            ]
            if matching_items:
                created = matching_items[0]
                created_id = str(created.get("id") or "")
                address = str(created.get("floating_ip_address", ""))
                if not created_id:
                    raise SystemExit(f"Create response missing id: {created}")
                match_kept = True
                for extra_item in created_items:
                    extra_id = str(extra_item.get("id") or "")
                    if not extra_id or extra_id == created_id:
                        continue
                    cleanup_created_ip(token, extra_id, str(extra_item.get("floating_ip_address", "")))
                emit(
                    args,
                    {
                        "matched": True,
                        "attempt": attempt,
                        "batch_size": len(created_items),
                        "ip_list_dir": str(list_dir),
                        "ip": created,
                    },
                    compact_line=(
                        f"[{attempt}/{attempts_label(args.max_attempts)}] "
                        f"batch={len(created_items)} match ip={address} id={created.get('id')} kept"
                    ),
                )
                matched_message = (
                    "Найден новый "
                    "подходящий floating IP.\n"
                    f"IP: {address}\n"
                    f"ID: {created.get('id')}\n"
                    f"Region: {region}\n"
                    f"Project: {project_id}\n"
                    f"Attempt: {attempt}/{attempts_label(args.max_attempts)}\n"
                    f"Batch size: {len(created_items)}\n"
                    f"Source list: {list_dir}"
                )
                decision = resolve_match_action(matched_message)
                if decision == "keep_continue":
                    notify_success(matched_message + "\nDecision: keep and continue.")
                    continue
                if decision == "delete_continue":
                    match_kept = False
                    cleanup_created_ip(token, created_id, address)
                    created_items = []
                    notify_success(matched_message + "\nDecision: delete and continue.")
                    continue
                if decision == "telegram_unavailable":
                    write_pending_match(
                        args,
                        {
                            "reason": "telegram_unavailable",
                            "ip": address,
                            "id": created_id,
                            "region": region,
                            "project_id": project_id,
                            "attempt": attempt,
                            "batch_size": len(created_items),
                            "source_list": str(list_dir),
                            "created": created,
                            "created_at": utc_now(),
                        },
                    )
                    print(matched_message + "\nDecision: keep and stop because Telegram is unavailable.")
                    return 0
                notify_success(matched_message + "\nDecision: keep and stop.")
                return 0

            for item in created_items:
                cleanup_created_ip(token, str(item.get("id") or ""), str(item.get("floating_ip_address", "")))
            deleted_items = [
                {
                    "id": str(item.get("id") or ""),
                    "ip": str(item.get("floating_ip_address", "")),
                }
                for item in created_items
                if item.get("id")
            ]
            created_items = []
            post_create_sleep = sleep_with_jitter(
                env_float("SELECTEL_POST_CREATE_MIN_DELAY_SECONDS", 8.0),
                env_float("SELECTEL_POST_CREATE_MAX_DELAY_SECONDS", 15.0),
            )
            next_sleep = 0.0
            if (args.max_attempts <= 0 or attempt < args.max_attempts) and args.delay_seconds > 0:
                next_sleep = random.uniform(
                    args.delay_seconds,
                    args.delay_seconds + env_float("SELECTEL_DELAY_JITTER_SECONDS", 3.0),
                )
            emit(
                args,
                {
                    "matched": False,
                    "attempt": attempt,
                    "batch_size": len(deleted_items),
                    "deleted": deleted_items,
                    "post_create_sleep_seconds": round(post_create_sleep or 0.0, 1),
                    "next_sleep_seconds": round(next_sleep, 1),
                },
                compact_line=(
                    f"attempt {attempt} -> batch={len(deleted_items)} deleted -> sleeping {next_sleep:.1f}s"
                    if next_sleep > 0
                    else f"attempt {attempt} -> batch={len(deleted_items)} deleted"
                ),
            )
            if next_sleep > 0:
                time.sleep(next_sleep)
        except ApiError as error:
            if is_rate_limit_error(error):
                backoff_sec = random.uniform(
                    env_float("SELECTEL_RATE_LIMIT_BACKOFF_MIN_SECONDS", 300.0),
                    env_float("SELECTEL_RATE_LIMIT_BACKOFF_MAX_SECONDS", 600.0),
                )
                emit(
                    args,
                    {
                        "rate_limited": True,
                        "status_code": error.status_code,
                        "attempt": attempt,
                        "sleep_seconds": round(backoff_sec, 1),
                        "details": error.details.strip() or "<empty>",
                    },
                    compact_line=(
                        f"attempt {attempt} -> rate limited "
                        f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                    ),
                )
                time.sleep(backoff_sec)
                continue
            if is_transient_http_error(error):
                backoff_sec = random.uniform(
                    env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                    env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                )
                emit(
                    args,
                    {
                        "transient_error": True,
                        "status_code": error.status_code,
                        "attempt": attempt,
                        "sleep_seconds": round(backoff_sec, 1),
                        "details": error.details.strip() or "<empty>",
                    },
                    compact_line=(
                        f"attempt {attempt} -> HTTP {error.status_code} "
                        f"({error.details.strip() or 'transient error'}), retry after {backoff_sec:.0f}s"
                    ),
                )
                time.sleep(backoff_sec)
                continue
            if is_empty_request_error(error):
                backoff_sec = random.uniform(
                    env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                    env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                )
                emit(
                    args,
                    {
                        "request_error": True,
                        "attempt": attempt,
                        "sleep_seconds": round(backoff_sec, 1),
                        "details": error.details.strip() or "<empty>",
                    },
                    compact_line=(
                        f"attempt {attempt} -> request error "
                        f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                    ),
                )
                time.sleep(backoff_sec)
                continue
            if is_resource_not_found_error(error):
                backoff_sec = random.uniform(
                    env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                    env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                )
                emit(
                    args,
                    {
                        "resource_not_found": True,
                        "status_code": error.status_code,
                        "attempt": attempt,
                        "sleep_seconds": round(backoff_sec, 1),
                        "details": error.details.strip() or "<empty>",
                    },
                    compact_line=(
                        f"attempt {attempt} -> resource not found "
                        f"({error.details.strip() or '<empty>'}), retry after {backoff_sec:.0f}s"
                    ),
                )
                time.sleep(backoff_sec)
                continue
            if is_project_locked_error(error):
                backoff_sec = random.uniform(
                    env_float("SELECTEL_BACKOFF_BASE_SECONDS", 10.0),
                    env_float("SELECTEL_BACKOFF_CAP_SECONDS", 120.0),
                )
                emit(
                    args,
                    {
                        "project_locked": True,
                        "status_code": error.status_code,
                        "attempt": attempt,
                        "sleep_seconds": round(backoff_sec, 1),
                    },
                    compact_line=(
                        f"attempt {attempt} -> project locked "
                        f"({error.details.strip() or 'project_is_locked'}), retry after {backoff_sec:.0f}s"
                    ),
                )
                time.sleep(backoff_sec)
                continue
            raise
        except KeyboardInterrupt:
            for item in created_items:
                cleanup_created_ip(token, str(item.get("id") or ""), str(item.get("floating_ip_address", "")))
            raise
        except Exception:
            if not match_kept:
                for item in created_items:
                    cleanup_created_ip(token, str(item.get("id") or ""), str(item.get("floating_ip_address", "")))
            raise

    raise SystemExit(f"No matching IP found after {attempt} attempts.")


def cmd_delete(token: str, args: argparse.Namespace) -> int:
    floatingip_id = args.id
    target_ip = args.ip
    if not floatingip_id:
        if not target_ip:
            raise SystemExit("Specify --id or --ip")
        items = list_floating_ips(token)
        matches = [item for item in items if item.get("floating_ip_address") == target_ip]
        if not matches:
            raise SystemExit(f"Floating IP not found by address: {target_ip}")
        floatingip_id = str(matches[0]["id"])
    if args.dry_run:
        emit(
            args,
            {"dry_run": True, "id": floatingip_id, "ip": target_ip},
            compact_line=f"dry-run delete id={floatingip_id} ip={target_ip or '-'}",
        )
        return 0
    delete_floating_ip(token, str(floatingip_id))
    emit(
        args,
        {"deleted": True, "id": floatingip_id, "ip": target_ip},
        compact_line=f"deleted id={floatingip_id} ip={target_ip or '-'}",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Selectel floating IP helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("auth-check", help="Validate X-Token and list projects")

    list_parser = subparsers.add_parser("list", help="List floating IPs")
    list_parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON")
    list_parser.add_argument("--project-id", help="Filter by project id")
    list_parser.add_argument("--status", help="Filter by status")
    list_parser.add_argument("--ip", help="Find exact IP")
    list_parser.add_argument("--prefix", help="Find by IP prefix")

    find_parser = subparsers.add_parser("find", help="Find floating IPs against local ip folder")
    find_parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON")
    find_parser.add_argument("--project-id", help="Filter by project id")
    find_parser.add_argument("--status", help="Filter by status")
    find_parser.add_argument("--ip", help="Find exact IP")
    find_parser.add_argument("--prefix", help="Find by IP prefix")
    find_parser.add_argument(
        "--local-list",
        action="store_true",
        help="Return only IPs present in the local ip list folder",
    )
    find_parser.add_argument(
        "--ip-list-dir",
        default=str(default_ip_list_dir()),
        help="Folder with *.txt IP and CIDR lists",
    )

    create_parser = subparsers.add_parser(
        "create",
        help="Create floating IPs one by one until one matches the local ip list folder",
    )
    create_parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON")
    create_parser.add_argument("--project-id", help="Target project id")
    create_parser.add_argument("--region", help="Region, for example ru-2")
    create_parser.add_argument(
        "--max-attempts",
        type=int,
        default=env_int("SELECTEL_MAX_ATTEMPTS", 100),
        help="How many create/delete attempts to make",
    )
    create_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=env_float("SELECTEL_DELAY_SECONDS", 2.0),
        help="Delay between failed attempts",
    )
    create_parser.add_argument(
        "--ip-list-dir",
        default=str(default_ip_list_dir()),
        help="Folder with *.txt IP and CIDR lists",
    )
    create_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the request without creating IPs",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete floating IP by id or ip")
    delete_parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON")
    delete_parser.add_argument("--id", help="Floating IP id")
    delete_parser.add_argument("--ip", help="Floating IP address")
    delete_parser.add_argument("--dry-run", action="store_true", help="Show the request without deleting the IP")

    return parser


def main() -> int:
    configure_stdio()
    load_env_file()
    token = str(env("SELECTEL_X_TOKEN"))
    args = build_parser().parse_args()
    args.log_path = None
    command_map = {
        "auth-check": cmd_auth_check,
        "list": cmd_list,
        "find": cmd_find,
        "create": cmd_create,
        "delete": cmd_delete,
    }
    try:
        return command_map[args.command](token, args)
    except KeyboardInterrupt:
        print("\nОперация отменена пользователем.")
        return 130
    except ApiError as error:
        details = error.details.strip() or "<empty>"
        status = f"HTTP {error.status_code}" if error.status_code is not None else "request error"
        raise SystemExit(f"{status}: {details}") from error
