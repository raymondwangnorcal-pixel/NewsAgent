from __future__ import annotations

import os
from datetime import date

from news_agent.notifications.base import ConfigurationError, NotificationSender


DEFAULT_DELIVERY_CHANNEL = "telegram"


def selected_channel(channel: str | None = None) -> str:
    value = channel or os.getenv("BRIEFING_DELIVERY_CHANNEL", DEFAULT_DELIVERY_CHANNEL)
    return value.strip().lower()


def resolve_sender(channel: str | None = None) -> tuple[NotificationSender, str, str]:
    resolved = selected_channel(channel)
    if resolved == "telegram":
        from news_agent.notifications.telegram import TelegramSender

        return TelegramSender.from_env(), os.getenv("TELEGRAM_CHAT_ID", ""), "Telegram"
    if resolved == "sms":
        from news_agent.notifications.sms import TwilioSender

        return TwilioSender.from_env(), os.getenv("BRIEFING_TO_NUMBER", ""), "SMS"
    raise ConfigurationError(
        f"Unsupported BRIEFING_DELIVERY_CHANNEL={resolved!r}. Use 'telegram' for Phase 1 or 'sms' for Phase 4."
    )


def send_briefing_messages(messages: list[str], channel: str | None = None) -> int:
    sender, recipient, _label = resolve_sender(channel)
    header = f"Morning Briefing - {date.today().isoformat()}"
    sender.send_message(recipient, header)
    for message in messages:
        sender.send_message(recipient, message)
    return len(messages) + 1


def send_telegram_test_message() -> None:
    from news_agent.notifications.telegram import TelegramSender

    sender = TelegramSender.from_env()
    recipient = os.getenv("TELEGRAM_CHAT_ID", "")
    sender.send_message(recipient, f"Morning News Agent Telegram test - {date.today().isoformat()}")
