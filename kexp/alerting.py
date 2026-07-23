"""Failure-alert email + run heartbeat.

Never logs secret values (SMTP username/password) — only presence is checked.
`smtp_factory` is injected so tests never touch a live SMTP connection.
"""
import json
import os
import smtplib
from email.message import EmailMessage


def write_heartbeat(path, *, now_str, counts) -> str:
    """Write `path` with `{timestamp, counts}` JSON; return the path."""
    dirname = os.path.dirname(path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    payload = {"timestamp": now_str, "counts": dict(counts)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def send_failure_email(config, *, subject, body, smtp_factory=smtplib.SMTP) -> bool:
    """Send a failure-alert email via `smtp_factory`; False when SMTP config is missing."""
    required = [
        config.smtp_host,
        config.smtp_port,
        config.smtp_username,
        config.smtp_password,
        config.mail_from,
        config.mail_to,
    ]
    if any(not value for value in required):
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.mail_from
    msg["To"] = config.mail_to
    msg.set_content(body)

    with smtp_factory(config.smtp_host, config.smtp_port, timeout=30) as s:
        s.ehlo()
        # STARTTLS must succeed before we authenticate — otherwise login() would
        # transmit the SMTP credentials in plaintext. If it raises, abort the
        # send entirely (propagate) rather than fall back to an unencrypted login.
        s.starttls()
        s.ehlo()
        s.login(config.smtp_username, config.smtp_password)
        s.send_message(msg)

    return True
