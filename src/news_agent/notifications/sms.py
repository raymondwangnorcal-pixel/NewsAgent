from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from news_agent.notifications.base import ConfigurationError


SMS_PHASE_4_MESSAGE = (
    "SMS is not configured yet. SMS is a Phase 4 channel and requires "
    "TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, and BRIEFING_TO_NUMBER."
)
TwilioClientFactory = Callable[[str, str], Any]


class TwilioSender:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        client_factory: TwilioClientFactory | None = None,
    ) -> None:
        missing = [
            name
            for name, value in (
                ("TWILIO_ACCOUNT_SID", account_sid),
                ("TWILIO_AUTH_TOKEN", auth_token),
                ("TWILIO_FROM_NUMBER", from_number),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(f"{SMS_PHASE_4_MESSAGE} Missing: {', '.join(missing)}.")
        self._from_number = from_number
        self._client = (client_factory or build_twilio_client)(account_sid, auth_token)

    @classmethod
    def from_env(cls) -> "TwilioSender":
        return cls(
            account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
            auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            from_number=os.getenv("TWILIO_FROM_NUMBER", ""),
        )

    def send_message(self, recipient: str, message: str) -> None:
        if not recipient:
            raise ConfigurationError(f"{SMS_PHASE_4_MESSAGE} Missing: BRIEFING_TO_NUMBER.")
        self._client.messages.create(body=message, from_=self._from_number, to=recipient)


def build_twilio_client(account_sid: str, auth_token: str) -> Any:
    try:
        from twilio.rest import Client
    except ImportError as exc:
        raise ConfigurationError(
            f"{SMS_PHASE_4_MESSAGE} Install provider dependencies with: "
            "python -m pip install -e '.[providers]'"
        ) from exc
    return Client(account_sid, auth_token)
