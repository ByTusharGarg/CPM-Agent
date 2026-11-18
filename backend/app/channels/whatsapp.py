"""WhatsApp Cloud API (Meta) channel.

Outbound:
* plain text (<= 4096 chars);
* interactive reply buttons for decisions (max 3 buttons, 20-char titles);
* an approved template when the user is outside the 24-hour customer-service
  window (Meta error 131047) - the full message is resent once they reply.
Inbound: see channels/routes.py (webhook) and channels/intake.py (routing).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Any

import httpx

from ..config import settings
from . import ChannelError

log = logging.getLogger(__name__)

REENGAGEMENT_CODES = {131047, 131026}  # outside 24h window / not deliverable as free-form


class WhatsAppError(ChannelError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def digits(phone: str) -> str:
    """Normalise to digits only: '+91 98000-00001' -> '919800000001' (WhatsApp's wa_id format)."""
    return re.sub(r"\D", "", phone or "")


def verify_signature(body: bytes, header: str | None, secret: str | None = None) -> bool:
    secret = secret if secret is not None else settings.wa_app_secret
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=20)
        return self._client

    def configured(self) -> bool:
        return bool(settings.wa_token and settings.wa_phone_number_id)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.wa_token}"}

    def _post(self, payload: dict[str, Any]) -> str:
        if not self.configured():
            raise ChannelError("WhatsApp not configured (set CPM_WA_TOKEN and CPM_WA_PHONE_NUMBER_ID)")
        url = f"{settings.wa_api_base}/{settings.wa_phone_number_id}/messages"
        try:
            r = self.client.post(url, json={"messaging_product": "whatsapp", **payload}, headers=self._headers())
        except httpx.HTTPError as e:
            raise WhatsAppError(f"network error: {e}") from e
        data = r.json() if r.content else {}
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            raise WhatsAppError(f"{err.get('code')}: {err.get('message', r.text[:200])}", err.get("code"))
        return (data.get("messages") or [{}])[0].get("id", "")

    # ------------------------------------------------------------------ outbound

    def send(self, to: str, subject: str, body: str, link: str = "", actions: list[dict] | None = None) -> str:
        to = digits(to)
        if not to:
            raise ChannelError("no WhatsApp number for recipient")
        head = f"*{subject}*\n\n" if subject else ""
        tail = f"\n\n{link}" if link else ""
        if actions and len(actions) <= 3:
            return self._post({
                "to": to,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": _clip(head + body + tail, 1024)},
                    "action": {"buttons": [
                        {"type": "reply", "reply": {"id": a["id"][:256], "title": _clip(a["label"], 20)}} for a in actions
                    ]},
                },
            })
        if actions:  # more than 3 choices: numbered list, user replies with the number
            body += "\n\n" + "\n".join(f"{i + 1}. {a['label']}" for i, a in enumerate(actions)) + "\n\nReply with the number."
        return self._post({"to": to, "type": "text", "text": {"preview_url": bool(link), "body": _clip(head + body + tail, 4096)}})

    def send_template(self, to: str, title: str) -> str:
        if not settings.wa_alert_template:
            raise ChannelError("no WhatsApp template configured (CPM_WA_ALERT_TEMPLATE)")
        return self._post({
            "to": digits(to),
            "type": "template",
            "template": {
                "name": settings.wa_alert_template,
                "language": {"code": settings.wa_template_lang},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": _clip(title, 900)}]}],
            },
        })

    # ------------------------------------------------------------------ media

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        try:
            meta = self.client.get(f"{settings.wa_api_base}/{media_id}", headers=self._headers())
            meta.raise_for_status()
            info = meta.json()
            blob = self.client.get(info["url"], headers=self._headers())
            blob.raise_for_status()
        except (httpx.HTTPError, KeyError) as e:
            raise WhatsAppError(f"media download failed: {e}") from e
        return blob.content, info.get("mime_type") or blob.headers.get("content-type", "application/octet-stream")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
