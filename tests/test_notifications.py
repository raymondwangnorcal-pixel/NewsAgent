from __future__ import annotations

import inspect

import pytest

import news_agent.pipeline as pipeline
from news_agent.notifications.base import ConfigurationError, NotificationSender
from news_agent.notifications.factory import (
    parse_recipient_list,
    resolve_sender,
    selected_channel,
    send_briefing_messages,
    send_telegram_test_message,
)
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


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_message(self, recipient: str, message: str) -> None:
        self.calls.append((recipient, message))


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
    monkeypatch.delenv("TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_FROM_NUMBER", raising=False)

    sender, recipients, label = resolve_sender()

    assert isinstance(sender, TelegramSender)
    assert recipients == ("123",)
    assert label == "Telegram"


def test_resolve_sender_telegram_uses_multiple_chat_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEFING_DELIVERY_CHANNEL", "telegram")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "single")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "123, 456,123")

    _sender, recipients, label = resolve_sender()

    assert recipients == ("123", "456")
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


def test_parse_recipient_list_strips_empty_values_and_dedupes() -> None:
    assert parse_recipient_list(" 123, ,456,123 ") == ("123", "456")


def test_send_briefing_messages_sends_each_message_to_each_recipient(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = FakeSender()
    monkeypatch.setattr(
        "news_agent.notifications.factory.resolve_sender",
        lambda channel=None: (sender, ("123", "456"), "Telegram"),
    )

    sent_count = send_briefing_messages(["first", "second"])

    assert sent_count == 6
    assert [recipient for recipient, _message in sender.calls] == ["123", "123", "123", "456", "456", "456"]
    assert sender.calls[0][1].startswith("Morning Briefing - ")
    assert sender.calls[3][1] == sender.calls[0][1]
    assert [message for _recipient, message in sender.calls[1:3]] == ["first", "second"]
    assert [message for _recipient, message in sender.calls[4:6]] == ["first", "second"]


def test_send_telegram_test_message_sends_to_all_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    class FakeTelegramSender:
        @classmethod
        def from_env(cls) -> "FakeTelegramSender":
            return cls()

        def send_message(self, recipient: str, message: str) -> None:
            sent.append((recipient, message))

    monkeypatch.setattr("news_agent.notifications.telegram.TelegramSender", FakeTelegramSender)
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "123,456")

    sent_count = send_telegram_test_message()

    assert sent_count == 2
    assert [recipient for recipient, _message in sent] == ["123", "456"]
    assert all(message.startswith("Morning News Agent Telegram test - ") for _recipient, message in sent)


def test_split_telegram_message_keeps_chunks_under_limit() -> None:
    chunks = split_telegram_message("alpha\n" * 20, max_chars=25)

    assert chunks
    assert all(len(chunk) <= 25 for chunk in chunks)


def test_pipeline_does_not_import_delivery_adapters() -> None:
    source = inspect.getsource(pipeline).lower()

    assert "telegram" not in source
    assert "notifications" not in source
