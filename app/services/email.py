"""
eventnxt-backend: app/services/email.py

Outbound email for EventNXT — ticket delivery, order lookups, and anything
else that needs to reach a buyer's inbox.

Deliberately plain SMTP (stdlib smtplib) rather than any provider's SDK:
SMTP is the one protocol every provider speaks, so the provider is pure
configuration (SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD /
MAIL_FROM config vars). Today that's Resend (smtp.resend.com); swapping to
SES, Postmark, or anyone else is a config-var edit with zero code changes.

Design rules for callers:
- send_email() RAISES on failure (EmailNotConfigured or EmailSendError).
  Callers on a critical path (e.g. the Stripe webhook) must catch and log,
  never let an email hiccup fail the surrounding transaction — the paid
  order is sacred, the email is retryable.
- Sends are synchronous and can take a couple of seconds (SMTP handshake).
  Do them AFTER the important database work is committed.
"""

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import settings

FROM_DISPLAY_NAME = "EventNXT"


class EmailNotConfigured(Exception):
    """SMTP settings are missing — sends are impossible until config vars are set."""


class EmailSendError(Exception):
    """The SMTP server rejected or failed the send."""


def is_email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password and settings.mail_from)


def send_email(to: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    """
    Send one email. Plain-text always; HTML added as the rich alternative
    when provided (clients that can't render HTML fall back to the text).
    """
    if not is_email_configured():
        raise EmailNotConfigured(
            "SMTP is not configured — set SMTP_HOST / SMTP_USER / SMTP_PASSWORD / MAIL_FROM config vars."
        )

    msg = EmailMessage()
    msg["From"] = formataddr((FROM_DISPLAY_NAME, settings.mail_from))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        # Port 587 + STARTTLS — the provider-standard path (and Heroku
        # blocks port 25, so plain unencrypted SMTP was never an option).
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailSendError(f"SMTP send to {to} failed: {exc}") from exc