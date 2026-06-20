"""Transactional email (verification / password reset).

If ``SMTP_HOST`` is unset (local dev), the link is logged instead of sent so the
flow still works without a mail server.
"""

from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings
from app.core.logging import get_logger

log = get_logger("email")


async def _send(to: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        log.info("email.dev_no_smtp", to=to, subject=subject, body=body)
        return

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user,
        password=settings.smtp_password,
        start_tls=True,
    )
    log.info("email.sent", to=to, subject=subject)


async def send_verification_email(to: str, token: str) -> None:
    settings = get_settings()
    link = f"{settings.oauth_redirect_base}/verify?token={token}"
    await _send(
        to,
        "Verify your OpenRAG account",
        f"Welcome to OpenRAG!\n\nConfirm your email address:\n{link}\n\n"
        "This link expires in 24 hours.",
    )


async def send_password_reset_email(to: str, token: str) -> None:
    settings = get_settings()
    link = f"{settings.oauth_redirect_base}/reset?token={token}"
    await _send(
        to,
        "Reset your OpenRAG password",
        f"If you requested a password reset, use this link:\n{link}\n\n"
        "If you didn't, you can ignore this email. The link expires in 1 hour.",
    )
