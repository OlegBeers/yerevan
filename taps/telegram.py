"""Telegram sendMessage with the spec §7 step 4 outcome rules, and one-per-series admin alerts."""
from __future__ import annotations

import hashlib
import html
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from taps.state import State

API = "https://api.telegram.org/bot{token}/sendMessage"
EDIT_API = "https://api.telegram.org/bot{token}/editMessageText"
MAX_RETRY_AFTER = 60      # never stall a run longer than this on a 429
DEFAULT_RETRY_AFTER = 1
MAX_TEXT = 4096           # Telegram message length limit


@dataclass
class SendOutcome:
    status: str                       # "sent" | "rejected" | "unknown"
    description: str = ""
    migrate_to_chat_id: int | None = None
    message_id: int | None = None


def _retry_after(body: dict) -> int:
    value = (body.get("parameters") or {}).get("retry_after")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_RETRY_AFTER
    return min(value, MAX_RETRY_AFTER)


def _post_once(post: Callable[..., Any], url: str, payload: dict, timeout: float) -> SendOutcome | dict:
    """Return a final "unknown" outcome, or the parsed Telegram body (a dict with a bool "ok")."""
    try:
        resp = post(url, json=payload, timeout=timeout)
    except requests.RequestException as e:
        # the message may embed the request URL, which contains the bot token: the type name only
        return SendOutcome("unknown", type(e).__name__)
    if resp.status_code >= 500:
        return SendOutcome("unknown", f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError:
        return SendOutcome("unknown", f"HTTP {resp.status_code}: ответ не JSON")
    if not isinstance(body, dict) or not isinstance(body.get("ok"), bool):
        return SendOutcome("unknown", f"HTTP {resp.status_code}: непонятный ответ")
    body.setdefault("error_code", resp.status_code)
    return body


def send_message(token: str, chat_id: str, html: str, button: tuple[str, str] | None = None, silent: bool = True,
                 post: Callable[..., Any] = requests.post, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 30) -> SendOutcome:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": html,
        "parse_mode": "HTML",
        "disable_notification": silent,
        "link_preview_options": {"is_disabled": True},
    }
    if button:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    url = API.format(token=token)

    body = _post_once(post, url, payload, timeout)
    if isinstance(body, dict) and not body["ok"] and body["error_code"] == 429:
        sleep(_retry_after(body))
        body = _post_once(post, url, payload, timeout)
    if isinstance(body, SendOutcome):
        return body
    if body["ok"]:
        result = body.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            message_id = None
        return SendOutcome("sent", message_id=message_id)
    migrate = (body.get("parameters") or {}).get("migrate_to_chat_id")
    if isinstance(migrate, bool) or not isinstance(migrate, int):
        migrate = None
    return SendOutcome("rejected", str(body.get("description", "")), migrate)


def edit_message(token: str, chat_id: str, message_id: int, html: str, button: tuple[str, str] | None = None,
                 post: Callable[..., Any] = requests.post, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 30) -> SendOutcome:
    """Edit a previously sent message in place (editMessageText): no new message, no notification.
    reply_markup must be resent even when the text alone changed, or Telegram drops the existing button."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": html,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if button:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": button[0], "url": button[1]}]]}
    url = EDIT_API.format(token=token)

    body = _post_once(post, url, payload, timeout)
    if isinstance(body, dict) and not body["ok"] and body["error_code"] == 429:
        sleep(_retry_after(body))
        body = _post_once(post, url, payload, timeout)
    if isinstance(body, SendOutcome):
        return body
    if body["ok"]:
        return SendOutcome("sent")
    description = str(body.get("description", ""))
    if body["error_code"] == 400 and "message is not modified" in description:
        return SendOutcome("sent")
    migrate = (body.get("parameters") or {}).get("migrate_to_chat_id")
    if isinstance(migrate, bool) or not isinstance(migrate, int):
        migrate = None
    return SendOutcome("rejected", description, migrate)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class Alerter:
    """Admin alerts, one per series: a key is re-sent only when its text changes or after resolve()."""

    def __init__(self, state: State):
        self.state = state
        self.queue: dict[str, str] = {}

    def alert(self, key: str, text: str, dedupe_by_key: bool = False) -> None:
        """dedupe_by_key: any active alert on this key skips a new one, even if the text changed
        (a failing source's error text may vary run to run without starting a new series)."""
        if dedupe_by_key and key in self.state.alerts:
            return
        digest = _hash(text)
        if not dedupe_by_key and self.state.alerts.get(key) == digest:
            return
        self.state.alerts[key] = digest
        self.queue[key] = text

    def resolve(self, key: str) -> None:
        self.state.alerts.pop(key, None)
        self.queue.pop(key, None)

    def pending_text(self) -> str | None:
        if not self.queue:
            return None
        text = "⚠️ taps:"
        texts = list(self.queue.values())
        for i, alert in enumerate(texts):
            line = "\n• " + html.escape(alert)
            rest = len(texts) - i
            tail = f"\n…и ещё {rest} предупреждений не поместилось"
            if len(text) + len(line) + len(tail) > MAX_TEXT:
                return text + tail
            text += line
        return text

    def flush(self, send: Callable[[str], SendOutcome]) -> SendOutcome | None:
        """Send the queue as one message; None when there was nothing to send."""
        text = self.pending_text()
        if text is None:
            return None
        outcome = send(text)
        if outcome.status == "rejected":
            # The admin surely did not get it: forget the hashes so the next run tries again.
            for key in self.queue:
                self.state.alerts.pop(key, None)
        self.queue.clear()
        return outcome
