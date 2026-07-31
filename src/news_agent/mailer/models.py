from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DeliveryState = Literal["prepared", "sending", "smtp_accepted", "failed", "indeterminate"]
InstrumentType = Literal["stock", "adr", "etf"]


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    username: str
    app_password: str
    from_address: str
    recipients: tuple[str, ...]


@dataclass(frozen=True)
class EmailWatchlistEntry:
    ticker: str
    display_name: str
    instrument_type: InstrumentType
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecipientOutcome:
    recipient: str
    state: DeliveryState
    error_code: str = ""


@dataclass(frozen=True)
class EmailEdition:
    edition_id: int
    local_date: str
    revision: int
    subject: str
    plain_text: str
    html: str
    state: DeliveryState
