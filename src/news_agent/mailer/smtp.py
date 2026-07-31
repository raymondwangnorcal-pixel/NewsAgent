from __future__ import annotations

import smtplib
import socket
import ssl
from collections.abc import Callable
from email.message import EmailMessage

from news_agent.fetch import _ssl_context
from news_agent.mailer.models import EmailSettings, RecipientOutcome


SMTPFactory = Callable[[str, int, float], smtplib.SMTP]


def classify_smtp_error(exc: OSError) -> str:
    """Return stable, non-secret error codes suitable for delivery state."""
    if isinstance(exc, socket.gaierror):
        return "dns_failure"
    if isinstance(exc, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls_certificate_invalid"
    if isinstance(exc, ssl.SSLError):
        return "tls_handshake_failed"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "auth_failed"
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return "server_disconnected"
    if isinstance(exc, smtplib.SMTPException):
        return f"smtp_{type(exc).__name__.lower()}"
    return "network_error"


def build_message(
    subject: str,
    plain_text: str,
    html: str,
    settings: EmailSettings,
    recipient: str,
    *,
    message_id: str = "",
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.from_address
    message["To"] = recipient
    if message_id:
        message["Message-ID"] = message_id
    message.set_content(plain_text)
    message.add_alternative(html, subtype="html")
    return message


def send_email(
    settings: EmailSettings,
    recipient: str,
    subject: str,
    plain_text: str,
    html: str,
    timeout_seconds: float = 20.0,
    smtp_factory: SMTPFactory | None = None,
    message_id: str = "",
) -> RecipientOutcome:
    factory = smtp_factory or _default_smtp_factory
    message = build_message(subject, plain_text, html, settings, recipient, message_id=message_id)
    smtp: smtplib.SMTP | None = None
    data_started = False
    try:
        smtp = factory(settings.host, settings.port, timeout_seconds)
        smtp.ehlo()
        if settings.port == 587:
            smtp.starttls(context=_ssl_context())
            smtp.ehlo()
        smtp.login(settings.username, settings.app_password)
        mail_code, _mail_response = smtp.mail(settings.from_address)
        if mail_code != 250:
            return RecipientOutcome(recipient, "failed", f"smtp_mail_{mail_code}")
        rcpt_code, _rcpt_response = smtp.rcpt(recipient)
        if rcpt_code not in {250, 251}:
            return RecipientOutcome(recipient, "failed", f"smtp_rcpt_{rcpt_code}")
        data_started = True
        data_code, _data_response = smtp.data(message.as_bytes())
        if data_code != 250:
            return RecipientOutcome(recipient, "failed", f"smtp_data_{data_code}")
        return RecipientOutcome(recipient, "smtp_accepted")
    except OSError as exc:
        state = "indeterminate" if data_started else "failed"
        return RecipientOutcome(recipient, state, classify_smtp_error(exc))
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except OSError:
                pass


def _default_smtp_factory(host: str, port: int, timeout: float) -> smtplib.SMTP:
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=_ssl_context())
    return smtplib.SMTP(host, port, timeout=timeout)
