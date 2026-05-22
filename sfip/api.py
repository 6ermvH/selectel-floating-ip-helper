"""Selectel REST API client, retries, error classifiers, and high-level cleanup."""

from __future__ import annotations

import http.client
import ipaddress
import json
import random
import time
import urllib.error
import urllib.request

from .config import env_float, env_int
from .log import logger
from .matchers import address_matches_local_lists

API_BASE = "https://api.selectel.ru/vpc/resell/v2"
TRANSIENT_HTTP_STATUS_CODES = {408, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: str = "",
        retry_after: float | None = None,
    ) -> None:
        self.status_code = status_code
        self.details = details
        # Most recent Retry-After value observed for this request, in seconds.
        # Set when the API returns 429 (rate limit) so callers can align their
        # top-level backoff with what Selectel asked for.
        self.retry_after = retry_after
        super().__init__(message)


def sleep_with_jitter(min_seconds: float, max_seconds: float) -> float | None:
    if max_seconds <= 0:
        return None
    if min_seconds < 0:
        min_seconds = 0
    if max_seconds < min_seconds:
        max_seconds = min_seconds
    duration = random.uniform(min_seconds, max_seconds)
    time.sleep(duration)
    return duration


def api_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    headers = {
        "X-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    max_retries = env_int("SELECTEL_API_RETRIES", 8)
    backoff_base = env_float("SELECTEL_BACKOFF_BASE_SECONDS", 5.0)
    backoff_cap = env_float("SELECTEL_BACKOFF_CAP_SECONDS", 90.0)
    request_timeout = env_float("SELECTEL_HTTP_TIMEOUT_SECONDS", 30.0)

    for attempt in range(1, max_retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                body = response.read().decode("utf-8")
                if not body.strip():
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            if error.code == 429:
                # Selectel's VPC Resell API does not include Retry-After
                # (observed 2026-05); even when it does, the window is on
                # the order of 10 min, far longer than this loop's
                # backoff_cap. Don't retry here — raise with the parsed
                # Retry-After (if any) so the caller can wait properly.
                raw_retry_after = error.headers.get("Retry-After")
                parsed_retry_after: float | None = None
                if raw_retry_after:
                    try:
                        parsed_retry_after = float(raw_retry_after)
                    except ValueError:
                        parsed_retry_after = None
                logger.warning(
                    "rate-limited (429) on %s %s; Retry-After=%s",
                    method,
                    url,
                    raw_retry_after if raw_retry_after else "<absent>",
                )
                raise ApiError(
                    f"{method} {url} failed",
                    status_code=error.code,
                    details=details,
                    retry_after=parsed_retry_after,
                ) from error
            if error.code in TRANSIENT_HTTP_STATUS_CODES and attempt < max_retries:
                wait_seconds = min(backoff_cap, backoff_base * (2 ** (attempt - 1)))
                sleep_with_jitter(wait_seconds, wait_seconds + 3.0)
                continue
            raise ApiError(f"{method} {url} failed", status_code=error.code, details=details) from error
        except urllib.error.URLError as error:
            if attempt < max_retries:
                wait_seconds = min(backoff_cap, backoff_base * max(1, attempt))
                sleep_with_jitter(wait_seconds, wait_seconds + 2.0)
                continue
            raise ApiError(f"network error: {error}") from error
        except TimeoutError as error:
            if attempt < max_retries:
                wait_seconds = min(backoff_cap, backoff_base * max(1, attempt))
                sleep_with_jitter(wait_seconds, wait_seconds + 2.0)
                continue
            raise ApiError(f"request timeout after {request_timeout:.1f}s: {method} {url}") from error
        except (
            ConnectionError,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            EOFError,
            http.client.HTTPException,
        ) as error:
            if attempt < max_retries:
                wait_seconds = min(backoff_cap, backoff_base * max(1, attempt))
                sleep_with_jitter(wait_seconds, wait_seconds + 2.0)
                continue
            raise ApiError(f"request error: {error}") from error

    raise SystemExit(f"Request failed after retries: {method} {url}")


def list_projects(token: str) -> list[dict]:
    return api_request("GET", "/projects", token).get("projects", [])


def list_floating_ips(token: str, *, detailed: bool = True) -> list[dict]:
    suffix = "?detailed=true" if detailed else ""
    return api_request("GET", f"/floatingips{suffix}", token).get("floatingips", [])


def create_floating_ips(token: str, project_id: str, region: str, quantity: int = 1) -> list[dict]:
    payload = {"floatingips": [{"quantity": quantity, "region": region}]}
    result = api_request("POST", f"/floatingips/projects/{project_id}", token, payload=payload)
    items = result.get("floatingips", [])
    if not items:
        raise SystemExit("Create request returned no floating IPs")
    return items


def delete_floating_ip(token: str, floatingip_id: str) -> None:
    try:
        api_request("DELETE", f"/floatingips/{floatingip_id}", token)
    except SystemExit:
        pass
    except ApiError as error:
        if error.status_code == 404:
            pass
        else:
            raise


def is_quota_exceeded_error(error: ApiError) -> bool:
    if error.status_code != 409:
        return False
    try:
        payload = json.loads(error.details)
    except json.JSONDecodeError:
        return "quota_exceeded" in error.details
    return payload.get("error") == "quota_exceeded"


def is_project_locked_error(error: ApiError) -> bool:
    if error.status_code != 400:
        return False
    try:
        payload = json.loads(error.details)
    except json.JSONDecodeError:
        return "project_is_locked" in error.details
    return payload.get("error") == "project_is_locked"


def is_empty_request_error(error: ApiError) -> bool:
    return error.status_code is None


def is_rate_limit_error(error: ApiError) -> bool:
    if error.status_code != 429:
        return False
    try:
        payload = json.loads(error.details)
    except json.JSONDecodeError:
        return "too_many_requests" in error.details or "rate" in error.details.lower()
    return payload.get("error") in {"too_many_requests", "rate_limit_exceeded"}


def is_transient_http_error(error: ApiError) -> bool:
    return error.status_code in TRANSIENT_HTTP_STATUS_CODES


def is_resource_not_found_error(error: ApiError) -> bool:
    if error.status_code != 404:
        return False
    try:
        payload = json.loads(error.details)
    except json.JSONDecodeError:
        return "resource_not_found" in error.details or "resource_quota_not_found" in error.details
    return payload.get("error") in {"resource_not_found", "resource_quota_not_found"}


def project_floating_ips(ips: list[dict], project_id: str) -> list[dict]:
    return [item for item in ips if item.get("project_id") == project_id]


def planned_batch_size(token: str, project_id: str) -> tuple[int, list[dict]]:
    batch_limit = env_int("SELECTEL_CREATE_BATCH_SIZE", 12)
    quota_limit = env_int("SELECTEL_FLOATING_IP_QUOTA", 12)
    if batch_limit <= 0:
        batch_limit = 1
    cached_ips: list[dict] = []
    for attempt in range(1, 4):
        try:
            cached_ips = list_floating_ips(token)
            break
        except ApiError as error:
            if error.status_code in {500, 502, 503, 504} and attempt < 3:
                wait = min(30.0, 2.0 * (2 ** (attempt - 1)))
                logger.warning(
                    "planned_batch_size: HTTP %s, retry %d/3 in %.1fs",
                    error.status_code,
                    attempt,
                    wait,
                )
                time.sleep(wait)
            else:
                raise
    current_used = len(project_floating_ips(cached_ips, project_id))
    available = max(0, quota_limit - current_used)
    size = max(1, min(batch_limit, available if available > 0 else batch_limit))
    return size, cached_ips


def cleanup_created_ip(token: str, floatingip_id: str | None, address: str | None = None) -> None:
    if not floatingip_id:
        return
    try:
        delete_floating_ip(token, floatingip_id)
    except ApiError as error:
        if error.status_code == 404:
            return
        target = f"{address} " if address else ""
        logger.warning(
            "cleanup delete failed for %sid=%s: HTTP %s",
            target,
            floatingip_id,
            error.status_code,
        )
    except Exception as error:
        target = f"{address} " if address else ""
        logger.warning("cleanup delete failed for %sid=%s: %s", target, floatingip_id, error)


def cleanup_nonmatching_project_ips(
    token: str,
    project_id: str,
    ip_set: set[str],
    networks: list[ipaddress._BaseNetwork],
    ips: list[dict] | None = None,
) -> list[dict]:
    deleted: list[dict] = []
    for item in (ips if ips is not None else list_floating_ips(token)):
        if item.get("project_id") != project_id:
            continue
        address = str(item.get("floating_ip_address", ""))
        if address_matches_local_lists(address, ip_set, networks):
            continue
        floatingip_id = str(item.get("id") or "")
        if not floatingip_id:
            continue
        delete_floating_ip(token, floatingip_id)
        deleted.append(
            {
                "id": floatingip_id,
                "ip": address,
                "status": item.get("status"),
                "region": item.get("region"),
            }
        )
    return deleted


def find_existing_matching_ip(
    token: str,
    project_id: str,
    ip_set: set[str],
    networks: list[ipaddress._BaseNetwork],
) -> dict | None:
    for item in list_floating_ips(token):
        if item.get("project_id") != project_id:
            continue
        if address_matches_local_lists(str(item.get("floating_ip_address", "")), ip_set, networks):
            return item
    return None
