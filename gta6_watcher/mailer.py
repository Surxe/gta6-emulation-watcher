"""SMTP delivery (Gmail App Password over STARTTLS).

Credentials and the recipient come only from the environment. On the home-server
that is /etc/home-server/gta6-watcher.env (root 600), loaded by the systemd unit;
same keys as the host-alerts env so it can be staged by copying that file.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Mapping, Optional

REQUIRED = ("SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_TO")


@dataclass(frozen=True)
class EmailConfig:
    host: str
    port: int
    user: str
    password: str
    mail_from: str
    mail_to: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Optional["EmailConfig"]:
        """None (email off) unless every required key is set."""
        if any(not env.get(k) for k in REQUIRED):
            return None
        return cls(
            host=env.get("SMTP_HOST") or "smtp.gmail.com",
            port=int(env.get("SMTP_PORT") or 587),
            user=env["SMTP_USER"],
            password=env["SMTP_PASS"],
            mail_from=env["MAIL_FROM"],
            mail_to=env["MAIL_TO"],
        )


def missing_keys(env: Mapping[str, str] = os.environ) -> list:
    return [k for k in REQUIRED if not env.get(k)]


def compose(cfg: EmailConfig, subject: str, text: str, html_body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.mail_from
    msg["To"] = cfg.mail_to
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send(
    cfg: EmailConfig,
    msg: EmailMessage,
    smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
) -> None:
    """Deliver; raises smtplib.SMTPException / OSError on failure."""
    with smtp_factory(cfg.host, cfg.port, timeout=30) as server:
        server.starttls()
        server.login(cfg.user, cfg.password)
        server.send_message(msg)
