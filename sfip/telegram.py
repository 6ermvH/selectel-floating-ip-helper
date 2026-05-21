"""Telegram bot interactions: notifications and inline-keyboard confirmation."""

from __future__ import annotations

import json
import secrets
import sys
import time
import urllib.request

from .config import env, env_flag, env_float, env_int, normalize_chat_id


def telegram_enabled() -> bool:
    bot_token = str(env("TELEGRAM_BOT_TOKEN", required=False, default="") or "").strip()
    chat_id = str(env("TELEGRAM_CHAT_ID", required=False, default="") or "").strip()
    return bool(bot_token and chat_id)


def telegram_confirmation_enabled() -> bool:
    return telegram_enabled() and env_flag("SELECTEL_TELEGRAM_CONFIRM_MATCH", default=False)


def telegram_api_request(method: str, payload: dict | None = None) -> dict:
    bot_token = str(env("TELEGRAM_BOT_TOKEN", required=False, default="") or "").strip()
    chat_id = str(env("TELEGRAM_CHAT_ID", required=False, default="") or "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("Telegram is not configured")

    request_timeout = env_float("SELECTEL_HTTP_TIMEOUT_SECONDS", 30.0)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=request_timeout) as response:
        body = response.read().decode("utf-8", errors="replace").strip()
        result = json.loads(body) if body else {}
    if not result.get("ok", False):
        description = result.get("description") or f"telegram {method} failed"
        raise RuntimeError(description)
    return result


def send_telegram_message(message: str, *, reply_markup: dict | None = None) -> dict | None:
    bot_token = str(env("TELEGRAM_BOT_TOKEN", required=False, default="") or "").strip()
    chat_id = str(env("TELEGRAM_CHAT_ID", required=False, default="") or "").strip()
    if not bot_token or not chat_id:
        return None

    payload: dict[str, object] = {"chat_id": chat_id, "text": message}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        return telegram_api_request("sendMessage", payload)
    except Exception as error:
        print(f"Telegram notify failed: {error}", file=sys.stderr)
        return None


def notify_success(message: str) -> None:
    print(message)
    send_telegram_message(message)


def safe_telegram_call(method: str, payload: dict | None = None) -> dict | None:
    try:
        return telegram_api_request(method, payload)
    except Exception as error:
        print(f"Telegram {method} failed: {error}", file=sys.stderr)
        return None


def get_telegram_updates(offset: int | None = None, timeout: int = 0) -> list[dict]:
    payload: dict[str, object] = {"timeout": timeout, "allowed_updates": ["callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    result = telegram_api_request("getUpdates", payload)
    updates = result.get("result", [])
    return updates if isinstance(updates, list) else []


def next_telegram_update_offset() -> int | None:
    try:
        updates = get_telegram_updates(timeout=0)
    except Exception as error:
        print(f"Telegram update probe failed: {error}", file=sys.stderr)
        return None
    if not updates:
        return None
    return max(int(update.get("update_id", 0)) for update in updates) + 1


def answer_telegram_callback(callback_query_id: str, text: str) -> None:
    safe_telegram_call(
        "answerCallbackQuery",
        {"callback_query_id": callback_query_id, "text": text, "show_alert": False},
    )


def edit_telegram_message(chat_id: str, message_id: int, text: str) -> None:
    safe_telegram_call(
        "editMessageText",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        },
    )


def wait_for_telegram_match_confirmation(message: str) -> str | None:
    if not telegram_confirmation_enabled():
        return None

    decision_token = secrets.token_hex(6)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Сохранить и искать дальше", "callback_data": f"keep_continue:{decision_token}"},
                {"text": "Сохранить и остановить", "callback_data": f"keep_stop:{decision_token}"},
            ],
            [
                {"text": "Удалить и искать дальше", "callback_data": f"delete_continue:{decision_token}"},
            ],
        ]
    }
    sent = send_telegram_message(message, reply_markup=keyboard)
    if not sent:
        return "telegram_unavailable"

    sent_message = sent.get("result", {}) if isinstance(sent, dict) else {}
    message_id = int(sent_message.get("message_id", 0) or 0)
    expected_chat_id = normalize_chat_id(env("TELEGRAM_CHAT_ID", required=False, default=""))
    timeout_seconds = env_int("SELECTEL_TELEGRAM_CONFIRM_TIMEOUT_SECONDS", 600)
    default_action = str(
        env(
            "SELECTEL_TELEGRAM_CONFIRM_DEFAULT_ACTION",
            required=False,
            default="keep_stop",
        )
        or "keep_stop"
    ).strip().lower()
    valid_actions = {"keep_continue", "keep_stop", "delete_continue"}
    if default_action not in valid_actions:
        raise SystemExit(
            "SELECTEL_TELEGRAM_CONFIRM_DEFAULT_ACTION must be one of: "
            "keep_continue, keep_stop, delete_continue"
        )

    offset = next_telegram_update_offset()
    deadline = time.time() + max(1, timeout_seconds)

    while time.time() < deadline:
        poll_timeout = min(30, max(1, int(deadline - time.time())))
        try:
            updates = get_telegram_updates(offset=offset, timeout=poll_timeout)
        except Exception as error:
            print(f"Telegram confirmation polling failed: {error}", file=sys.stderr)
            time.sleep(3)
            continue

        for update in updates:
            update_id = int(update.get("update_id", 0) or 0)
            offset = update_id + 1
            callback_query = update.get("callback_query")
            if not isinstance(callback_query, dict):
                continue

            callback_data = str(callback_query.get("data") or "")
            callback_id = str(callback_query.get("id") or "")
            callback_message = callback_query.get("message") or {}
            callback_chat_id = normalize_chat_id((callback_message.get("chat") or {}).get("id"))

            if not callback_data.endswith(f":{decision_token}"):
                continue
            if callback_chat_id != expected_chat_id:
                answer_telegram_callback(callback_id, "Это решение не для этого чата.")
                continue

            action = callback_data.split(":", 1)[0]
            if action not in valid_actions:
                answer_telegram_callback(callback_id, "Неизвестное действие.")
                continue

            answer_telegram_callback(callback_id, f"Принято: {action}")
            if message_id:
                action_labels = {
                    "keep_continue": "Сохранить и искать дальше",
                    "keep_stop": "Сохранить и остановить",
                    "delete_continue": "Удалить и искать дальше",
                }
                edit_telegram_message(
                    expected_chat_id,
                    message_id,
                    f"{message}\n\nРешение: {action_labels[action]}",
                )
            return action

    if message_id:
        timeout_labels = {
            "keep_continue": "Сохранить и искать дальше",
            "keep_stop": "Сохранить и остановить",
            "delete_continue": "Удалить и искать дальше",
        }
        edit_telegram_message(
            expected_chat_id,
            message_id,
            f"{message}\n\nТаймаут ожидания. Применено действие по умолчанию: {timeout_labels[default_action]}",
        )
    return default_action


def resolve_match_action(message: str) -> str:
    decision = wait_for_telegram_match_confirmation(message)
    if decision:
        print(f"Telegram decision: {decision}")
        return decision
    return "keep_stop"


def confirm_continue_on_existing_match(address: str, floatingip_id: str) -> bool:
    if not sys.stdin.isatty():
        print(
            f"Найден подходящий "
            f"существующий IP {address} "
            f"(id={floatingip_id}). "
            "Обнаружен "
            "неинтерактивный "
            "режим, остановка "
            "без изменений.",
            file=sys.stderr,
        )
        return False

    prompt = (
        f"Найден подходящий "
        f"существующий IP {address} "
        f"(id={floatingip_id}). "
        "Продолжить "
        "поиск другого IP? [y/N]: "
    )
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes", "д", "да"}
