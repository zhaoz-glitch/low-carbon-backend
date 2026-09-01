"""Email sending service — Resend API first, SMTP fallback, console for dev.

Configuration comes from environment variables (see ``Config``):

* ``RESEND_API_KEY`` — Resend API key (https://resend.com/api-keys).  When
  set, emails go out through Resend's REST API (preferred, no SMTP needed).
* ``MAIL_SMTP_HOST`` / ``MAIL_SMTP_PORT``  — e.g. smtp.gmail.com / 465
  (fallback when RESEND_API_KEY is not set)
* ``MAIL_SMTP_USER`` / ``MAIL_SMTP_PASSWORD`` — SMTP login + password
* ``MAIL_FROM`` / ``MAIL_FROM_NAME`` — sender address / display name

If neither is configured, ``send_email`` logs the message body instead of
sending it, so local development (and tests) still run end-to-end without
any external credentials.
"""

import json
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from flask import current_app

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


def _resend_configured(app) -> bool:
    return bool(app.config.get("RESEND_API_KEY"))


def _send_via_resend(app, to: str, subject: str, html: str, text: str) -> bool:
    """POST to Resend's REST API.  Returns True on 2xx."""
    from_name = app.config.get("MAIL_FROM_NAME") or "低碳价值筛选器"
    from_addr = app.config.get("MAIL_FROM") or "onboarding@resend.dev"
    payload = json.dumps({
        "from": formataddr((from_name, from_addr)),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=payload,
        headers={
            "Authorization": "Bearer " + app.config["RESEND_API_KEY"],
            "Content-Type": "application/json",
            # Resend sits behind Cloudflare which blocks the default
            # Python-urllib User-Agent with a 403 (error 1010).
            "User-Agent": "resend-python/2.0.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        logger.info("Email sent to %s via Resend (id=%s)", to, body.get("id"))
        return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        logger.error(
            "Resend API error sending to %s: HTTP %s %s", to, e.code, detail
        )
    except Exception:
        logger.exception("Resend request failed for %s", to)
    return False


def _smtp_configured(app) -> bool:
    return bool(app.config.get("MAIL_SMTP_HOST") and app.config.get("MAIL_SMTP_USER"))


def send_email(to: str, subject: str, html: str, text: str) -> bool:
    """Send a message to ``to``.  Returns True if handled (sent or logged)."""
    app = current_app._get_current_object()
    from_name = app.config.get("MAIL_FROM_NAME") or "低碳价值筛选器"
    from_addr = app.config.get("MAIL_FROM") or app.config.get("MAIL_SMTP_USER") or "noreply@localhost"

    if _resend_configured(app):
        return _send_via_resend(app, to, subject, html, text)

    if not _smtp_configured(app):
        # Dev fallback: surface the message in the server log.
        logger.warning(
            "[MAIL DEV MODE] SMTP not configured — pretending to send.\n"
            "  To: %s\n  Subject: %s\n  Body:\n%s\n%s",
            to, subject, text, html,
        )
        return True

    host = app.config["MAIL_SMTP_HOST"]
    port = int(app.config.get("MAIL_SMTP_PORT", 465))
    user = app.config["MAIL_SMTP_USER"]
    password = app.config.get("MAIL_SMTP_PASSWORD") or ""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, from_addr))
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        if port == 465:
            # Implicit TLS (SMTPS)
            with smtplib.SMTP_SSL(host, port, timeout=15, context=context) as server:
                server.login(user, password)
                server.sendmail(from_addr, [to], msg.as_string())
        else:
            # STARTTLS on the plain port (e.g. 587)
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, password)
                server.sendmail(from_addr, [to], msg.as_string())
        logger.info("Email sent to %s via %s:%s", to, host, port)
        return True
    except Exception:
        logger.exception("Failed to send email to %s via %s:%s", to, host, port)
        return False


def send_password_reset_code(to: str, code: str) -> bool:
    """Send the 6-digit reset code with a friendly bilingual-ish template."""
    subject = "[低碳价值筛选器] 密码重置验证码"
    text = (
        f"你好，\n\n"
        f"你正在重置低碳价值筛选器的登录密码，验证码是：{code}\n"
        f"验证码 15 分钟内有效，请勿转发给他人。\n\n"
        f"如果不是你本人的操作，请忽略这封邮件。"
    )
    html = f"""\
<div style="font-family:-apple-system,'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;
            padding:24px;background:#f9fafb;border-radius:12px;color:#111827;">
  <h2 style="margin:0 0 12px;font-size:18px;">密码重置验证码</h2>
  <p style="font-size:14px;line-height:1.6;margin:0 0 16px;">
    你好，你正在重置 <strong>低碳价值筛选器</strong> 的登录密码。
  </p>
  <div style="text-align:center;margin:24px 0;">
    <span style="display:inline-block;padding:14px 28px;font-size:28px;font-weight:700;letter-spacing:6px;
                 background:#ecfdf5;color:#059669;border:1px dashed #34d399;border-radius:8px;">{code}</span>
  </div>
  <p style="font-size:13px;color:#6b7280;line-height:1.6;margin:0;">
    验证码 <strong>15 分钟内</strong> 有效，请勿转发给他人。如果不是你本人的操作，请忽略这封邮件。
  </p>
</div>"""
    return send_email(to, subject, html, text)
