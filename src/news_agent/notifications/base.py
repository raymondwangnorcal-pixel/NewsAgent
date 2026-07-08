from __future__ import annotations

from typing import Protocol, runtime_checkable


class NotificationError(RuntimeError):
    """Raised when a notification cannot be sent."""


class ConfigurationError(NotificationError):
    """Raised when a delivery channel is missing required configuration."""


@runtime_checkable
class NotificationSender(Protocol):
    def send_message(self, recipient: str, message: str) -> None:
        """Send one notification message to one recipient."""
