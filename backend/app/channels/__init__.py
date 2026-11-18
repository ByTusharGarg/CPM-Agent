"""Outbound/inbound messaging channels. Email ships in phase 7; WhatsApp in phase 8."""
from __future__ import annotations

from typing import Protocol


class ChannelError(Exception):
    pass


class Channel(Protocol):
    name: str

    def configured(self) -> bool: ...
    def send(self, to: str, subject: str, body: str, link: str = "") -> None: ...
