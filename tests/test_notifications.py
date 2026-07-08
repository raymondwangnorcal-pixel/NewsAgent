from __future__ import annotations

import inspect

import pytest

import news_agent.pipeline as pipeline
from news_agent.notifications.base import ConfigurationError, NotificationSender
from news_agent.notifications.factory import resolve_sender, selected_channel
from news_agent.notifications.sms import TwilioSender
from news_agent.notifications.telegram import TelegramSender, split_telegram_message


class FakeTwilioMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def create(self, body: str, from_: str, to: str) -> None:
        self.calls.append({"body": body, "from": from_, "to": to})


class FakeTwilioClient:
    def __init__(self) -> None:
        self.messages = FakeTwilioMessages()


def test_telegram_sender_implements_notification_interface() -> None:
    sender = TelegramSender("token", transport=lambda url, payload, timeout: {"ok": True})

    assert isinstance(sender, NotificationSender)


def test_sms_sender_implements_notification_interface() -> None:
    sender = TwilioSender(
        account_sid="AC123",
        auth_token="secret",
        from_number="+15555550123",
        client_factory=lambda sid, token: FakeTwilioClient(),
    )

    assert isinstance(sender, NotificationSender)


def test_resolve_sender_telegram_does_not_require_twilio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEFING_DELIVERY_CHANNEL", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)

    sender, recipient, label = resolve_sender()

    assert isinstance(sender, TelegramSender)
    assert recipient == "123"
    assert label == "Telegram"


def test_resolve_sender_sms_requires_sms_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEFING_DELIVERY_CHANNEL", "sms")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)

    with pytest.raises(ConfigurationError, match="SMS is not configured yet"):
        resolve_sender()


def test_selected_channel_defaults_to_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRIEFING_DELIVERY_CHANNEL", raising=False)

    assert selected_channel() == "telegram"


def test_split_telegram_message_keeps_chunks_under_limit() -> None:
    chunks = split_telegram_message("alpha\n" * 20, max_chars=25)

    assert chunks
    assert all(len(chunk) <= 25 for chunk in chunks)


def test_pipeline_does_not_import_delivery_adapters() -> None:
    source = inspect.getsource(pipeline).lower()

    assert "telegram" not in source
    assert "notifications" not in source
