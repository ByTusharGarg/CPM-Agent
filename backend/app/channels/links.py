"""Signed, expiring action links for email ("Approve" / "Option 2").

The link only opens a confirmation page; the action happens on POST, so mail
security scanners that pre-fetch links cannot trigger decisions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from ..config import settings

TTL_SECONDS = 7 * 24 * 3600


def _sig(payload: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(settings.secret_key.encode(), payload, hashlib.sha256).digest()).decode().rstrip("=")


def make_token(user_id: int, action_id: str, ttl: int = TTL_SECONDS) -> str:
    payload = json.dumps({"u": user_id, "a": action_id, "e": int(time.time()) + ttl}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + _sig(payload)


def read_token(token: str) -> tuple[int, str]:
    """Returns (user_id, action_id) or raises ValueError."""
    try:
        body, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception as e:  # noqa: BLE001
        raise ValueError("malformed link") from e
    if not hmac.compare_digest(sig, _sig(payload)):
        raise ValueError("invalid link")
    data = json.loads(payload)
    if data["e"] < time.time():
        raise ValueError("this link has expired")
    return int(data["u"]), str(data["a"])


def action_url(user_id: int, action_id: str) -> str:
    return f"{settings.public_api_url}/api/channels/email/act?t={make_token(user_id, action_id)}"
