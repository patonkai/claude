"""Outgoing mail: SMTP when configured, otherwise .eml files in the outbox directory."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import settings


def build_message(to: str, subject: str, body: str, reference: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"{settings.operator_name} <{settings.operator_email}>"
    msg["To"] = to
    msg["Reply-To"] = settings.operator_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(idstring=reference)
    msg["X-Purgo-Reference"] = reference
    msg.set_content(body)
    return msg


def send(to: str, subject: str, body: str, reference: str) -> str:
    """Send a message. Returns a short description of where it went."""
    msg = build_message(to, subject, body, reference)
    if settings.smtp_host:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return f"smtp:{settings.smtp_host}"
    settings.outbox_dir.mkdir(parents=True, exist_ok=True)
    path = settings.outbox_dir / f"{reference}-{msg['Message-ID'].strip('<>').split('@')[0]}.eml"
    path.write_bytes(bytes(msg))
    return f"outbox:{path}"
