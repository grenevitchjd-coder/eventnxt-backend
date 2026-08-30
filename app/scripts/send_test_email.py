"""
eventnxt-backend: app/scripts/send_test_email.py

One-off smoke test for the SMTP email service. Run it from the Heroku Run
console (More -> Run console -> bash):

    python -m app.scripts.send_test_email you@example.com

Sends a short test message to the given address through the configured
SMTP provider and prints exactly what happened. No HTTP endpoint on
purpose: an unauthenticated "send an email" URL is an open relay waiting
to be abused, and the Run console already requires Heroku access.
"""

import sys

from app.config import settings
from app.services.email import EmailNotConfigured, EmailSendError, send_email


def run():
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print("Usage: python -m app.scripts.send_test_email you@example.com", file=sys.stderr)
        sys.exit(1)

    to = sys.argv[1]
    print(f"SMTP host: {settings.smtp_host}:{settings.smtp_port}")
    print(f"From:      {settings.mail_from}")
    print(f"To:        {to}")
    print("Sending...")

    try:
        send_email(
            to=to,
            subject="EventNXT email test — it works",
            text_body=(
                "This is a test email from the EventNXT backend.\n\n"
                "If you're reading this, the SMTP pipeline (app -> "
                f"{settings.smtp_host} -> your inbox) is fully working, and "
                "ticket delivery emails will flow through this exact path.\n\n"
                "— EventNXT"
            ),
            html_body=(
                "<p>This is a test email from the <strong>EventNXT</strong> backend.</p>"
                "<p>If you're reading this, the SMTP pipeline (app &rarr; "
                f"{settings.smtp_host} &rarr; your inbox) is fully working, and "
                "ticket delivery emails will flow through this exact path.</p>"
                "<p>&mdash; EventNXT</p>"
            ),
        )
    except EmailNotConfigured as exc:
        print(f"NOT CONFIGURED: {exc}", file=sys.stderr)
        sys.exit(1)
    except EmailSendError as exc:
        print(f"SEND FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Sent successfully. Check the inbox (and spam folder, the first time).")


if __name__ == "__main__":
    run()