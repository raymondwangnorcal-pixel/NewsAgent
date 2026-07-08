from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from news_agent.notifications.base import ConfigurationError, NotificationError


TELEGRAM_MAX_MESSAGE_CHARS = 4096
SYSTEM_CA_BUNDLE = "/etc/ssl/cert.pem"
TelegramTransport = Callable[[str, bytes, float], dict[str, Any]]


class TelegramSender:
    def __init__(
        self,
        bot_token: str,
        timeout_seconds: float = 15.0,
        transport: TelegramTransport | None = None,
    ) -> None:
        if not bot_token:
            raise ConfigurationError("Missing TELEGRAM_BOT_TOKEN. Add it to your local .env file.")
        self._bot_token = bot_token
        self._timeout_seconds = timeout_seconds
        self._transport = transport or post_telegram_form

    @classmethod
    def from_env(cls) -> "TelegramSender":
        return cls(bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""))

    def send_message(self, recipient: str, message: str) -> None:
        if not recipient:
            raise ConfigurationError("Missing TELEGRAM_CHAT_ID. Add it to your local .env file.")
        for chunk in split_telegram_message(message):
            payload = urllib.parse.urlencode(
                {
                    "chat_id": recipient,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                }
            ).encode("utf-8")
            response = self._transport(self._api_url(), payload, self._timeout_seconds)
            if not response.get("ok"):
                description = response.get("description") or "unknown Telegram API failure"
                raise NotificationError(f"Telegram API failure: {description}")

    def _api_url(self) -> str:
        return f"https://api.telegram.org/bot{self._bot_token}/sendMessage"


def split_telegram_message(message: str, max_chars: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
    if max_chars <= 0:
        raise NotificationError("Telegram message too long: max_chars must be positive.")
    if not message:
        return ["(empty message)"]
    if len(message) <= max_chars:
        return [message]

    chunks: list[str] = []
    current = ""
    for line in message.splitlines(keepends=True):
        while len(line) > max_chars:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if len(current) + len(line) > max_chars:
            chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip())
    if any(len(chunk) > max_chars for chunk in chunks):
        raise NotificationError("Telegram message too long after splitting.")
    return chunks


def post_telegram_form(url: str, payload: bytes, timeout_seconds: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds, context=ssl_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise NotificationError(f"Telegram API failure: HTTP {exc.code}") from exc
        description = data.get("description") or f"HTTP {exc.code}"
        raise NotificationError(f"Telegram API failure: {description}") from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        raise NotificationError(f"Telegram API failure: {exc}") from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise NotificationError("Telegram API failure: response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise NotificationError("Telegram API failure: response was not a JSON object")
    return data


def ssl_context() -> ssl.SSLContext:
    configured_bundle = os.getenv("BRIEFING_CA_BUNDLE")
    if configured_bundle:
        return ssl.create_default_context(cafile=configured_bundle)
    if os.path.exists(SYSTEM_CA_BUNDLE):
        return ssl.create_default_context(cafile=SYSTEM_CA_BUNDLE)
    return ssl.create_default_context()
