from __future__ import annotations

import os
from email.utils import parseaddr

from news_agent.notifications.base import ConfigurationError
from news_agent.notifications.factory import parse_recipient_list

from news_agent.mailer.models import EmailSettings


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Missing {name}. Add it to your local .env file.")
    return value


def _validate_address(value: str, setting: str) -> str:
    _display, address = parseaddr(value)
    if not address or "@" not in address or address != value:
        raise ConfigurationError(f"Invalid {setting} email address.")
    return address


def email_settings_from_env() -> EmailSettings:
    port_text = _require_env("GMAIL_SMTP_PORT")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ConfigurationError("GMAIL_SMTP_PORT must be an integer.") from exc
    if port not in {465, 587}:
        raise ConfigurationError("GMAIL_SMTP_PORT must be 465 (SSL) or 587 (STARTTLS).")

    recipients = tuple(
        _validate_address(value, "EMAIL_TO")
        for value in parse_recipient_list(_require_env("EMAIL_TO"))
    )
    if not recipients:
        raise ConfigurationError("Missing EMAIL_TO. Add at least one recipient to your local .env file.")
    return EmailSettings(
        host=_require_env("GMAIL_SMTP_HOST"),
        port=port,
        username=_validate_address(_require_env("GMAIL_SMTP_USERNAME"), "GMAIL_SMTP_USERNAME"),
        app_password=_require_env("GMAIL_SMTP_APP_PASSWORD"),
        from_address=_validate_address(_require_env("EMAIL_FROM"), "EMAIL_FROM"),
        recipients=recipients,
    )
