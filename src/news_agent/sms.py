from __future__ import annotations

import os

from news_agent.notifications.sms import TwilioSender


def send_sms_messages(messages: list[str]) -> list[str]:
    to_number = require_env("BRIEFING_TO_NUMBER")
    sender = TwilioSender.from_env()
    for body in messages:
        sender.send_message(to_number, body)
    return []


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value
