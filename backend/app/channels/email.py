"""SMTP email channel. With no CPM_SMTP_HOST configured, sending raises
ChannelError and the notification stays in the outbox (visible in the UI).

Decision emails carry the reference in the subject ("[CPM alert:12] ...") so a
plain reply can be routed, plus signed links to a confirmation page.
"""
from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage

from ..config import settings
from . import ChannelError


class EmailChannel:
    name = "email"

    def configured(self) -> bool:
        return bool(settings.smtp_host)

    def send(self, to: str, subject: str, body: str, link: str = "", actions: list[dict] | None = None) -> str:
        if not self.configured():
            raise ChannelError("SMTP not configured (set CPM_SMTP_HOST)")
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        mid = f"<{uuid.uuid4().hex}@cpm>"
        msg["Message-ID"] = mid
        text = body
        if actions:
            text += "\n\nChoose:\n" + "\n".join(
                f"  {i + 1}. {a['label']}" + (f"\n     {a['url']}" if a.get("url") else "") for i, a in enumerate(actions)
            )
            text += "\n\nOr simply reply to this email with the number (e.g. \"2\")."
        if link:
            text += f"\n\nOpen in the app: {link}"
        msg.set_content(text)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            raise ChannelError(f"{type(e).__name__}: {e}") from e
        return mid
