from __future__ import annotations

import os
from datetime import date

from news_agent.notifications.base import ConfigurationError, NotificationSender


DEFAULT_DELIVERY_CHANNEL = "telegram"


def selected_channel(channel: str | None = None) -> str:
    value = channel or os.getenv("BRIEFING_DELIVERY_CHANNEL", DEFAULT_DELIVERY_CHANNEL)
    return value.strip().lower()


def parse_recipient_list(value: str) -> tuple[str, ...]:
    recipients = []
    seen: set[str] = set()
    for item in value.split(","):
        recipient = item.strip()
        if recipient and recipient not in seen:
            recipients.append(recipient)
            seen.add(recipient)
    return tuple(recipients)


def telegram_recipients() -> tuple[str, ...]:
    multi_recipient_value = os.getenv("TELEGRAM_CHAT_IDS", "")
    if multi_recipient_value.strip():
        return parse_recipient_list(multi_recipient_value)
    return parse_recipient_list(os.getenv("TELEGRAM_CHAT_ID", ""))


def _require_recipients(recipients: tuple[str, ...], env_names: str) -> tuple[str, ...]:
    if not recipients:
        raise ConfigurationError(f"Missing {env_names}. Add at least one recipient to your local .env file.")
    return recipients


def resolve_sender(channel: str | None = None) -> tuple[NotificationSender, tuple[str, ...], str]:
    resolved = selected_channel(channel)
    if resolved == "telegram":
        from news_agent.notifications.telegram import TelegramSender

        return (
            TelegramSender.from_env(),
            _require_recipients(telegram_recipients(), "TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID"),
            "Telegram",
        )
    if resolved == "sms":
        from news_agent.notifications.sms import TwilioSender

        return (
            TwilioSender.from_env(),
            _require_recipients(parse_recipient_list(os.getenv("BRIEFING_TO_NUMBER", "")), "BRIEFING_TO_NUMBER"),
            "SMS",
        )
    raise ConfigurationError(
        f"Unsupported BRIEFING_DELIVERY_CHANNEL={resolved!r}. Use 'telegram' for Phase 1 or 'sms' for Phase 4."
    )


def send_briefing_messages(messages: list[str], channel: str | None = None) -> int:
    sender, recipients, _label = resolve_sender(channel)
    header = f"Morning Briefing - {date.today().isoformat()}"
    sent_count = 0
    for recipient in recipients:
        sender.send_message(recipient, header)
        sent_count += 1
        for message in messages:
            sender.send_message(recipient, message)
            sent_count += 1
    return sent_count


def send_telegram_test_message() -> int:
    from news_agent.notifications.telegram import TelegramSender

    sender = TelegramSender.from_env()
    recipients = _require_recipients(telegram_recipients(), "TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID")
    for recipient in recipients:
        sender.send_message(recipient, f"Morning News Agent Telegram test - {date.today().isoformat()}")
    return len(recipients)
