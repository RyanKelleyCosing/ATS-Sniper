"""Email notification helpers for ATS Sniper status and alert messages."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any, Mapping, Sequence


EMAIL_TIMEOUT_SECONDS = 30


def send_status_email(
    config: Mapping[str, Any],
    subject: str,
    heading: str,
    message_lines: Sequence[str],
    stats: Mapping[str, Any] | None = None,
) -> bool:
    """Send a plain status email using the configured SMTP settings."""
    email_config = config.get("email", {})
    if not email_config.get("sender_email"):
        print("⚠️ Email not configured in config.json")
        return False

    html_lines = "".join(f"<p>{escape(line)}</p>" for line in message_lines)
    stats_html = ""
    if stats:
        stat_items = "".join(
            f"<li><strong>{escape(str(key))}:</strong> {escape(str(value))}</li>"
            for key, value in stats.items()
        )
        stats_html = f"<h2>Run Details</h2><ul>{stat_items}</ul>"

    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 720px; margin: 0 auto;">
        <div style="background: #1f2937; color: white; padding: 18px 20px; border-radius: 8px 8px 0 0;">
            <h1 style="margin: 0; font-size: 22px;">{escape(heading)}</h1>
        </div>
        <div style="border: 1px solid #d1d5db; border-top: 0; padding: 20px; border-radius: 0 0 8px 8px;">
            {html_lines}
            {stats_html}
        </div>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = email_config["sender_email"]
    msg["To"] = email_config["recipient_email"]
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(
            email_config["smtp_server"],
            email_config["smtp_port"],
            timeout=EMAIL_TIMEOUT_SECONDS,
        ) as server:
            server.starttls()
            server.login(email_config["sender_email"], email_config["sender_password"])
            server.sendmail(email_config["sender_email"], email_config["recipient_email"], msg.as_string())
        print("  ✅ Status email sent successfully!")
        return True
    except Exception as exc:
        print(f"  ❌ Status email failed: {exc}")
        return False