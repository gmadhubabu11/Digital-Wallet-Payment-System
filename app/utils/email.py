import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from fastapi import BackgroundTasks

from app.config import settings

log = logging.getLogger("wallet.email")


def send_email(to: str, subject: str, body: str) -> None:
    """Never raises: an email failure must not break a money action."""
    try:
        if not settings.SMTP_HOST:
            log.info("[EMAIL not configured] to=%s | %s | %s", to, subject, body)
            return
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = settings.SMTP_FROM, to, subject
        msg.set_content(body)
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception:
        log.exception("Failed to send email to %s (%s)", to, subject)


def notify(bg: Optional[BackgroundTasks], to: str, subject: str, body: str) -> None:
    if bg is not None:
        bg.add_task(send_email, to, subject, body)
    else:
        send_email(to, subject, body)
