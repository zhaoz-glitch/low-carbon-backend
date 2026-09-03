"""Transactional email helpers.

Railway blocks outbound SMTP; Resend uses HTTPS. Without RESEND_API_KEY
or SMTP settings the code is logged and treated as sent (local/dev).
"""

import logging
import random
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

logger = logging.getLogger(__name__)

DEFAULT_FROM = "onboarding@resend.dev"


def generate_reset_code() -> str:
    return f"{random.randint(100000, 999999)}"


def _bodies(code: str, user_name: str | None = None) -> tuple[str, str]:
    greeting = f"{user_name}，你好：" if user_name else "你好："
    html = f"""
    <html>
    <body style="font-family: Inter, -apple-system, BlinkMacSystemFont, sans-serif; color: #1e293b; background: #f8fafc; padding: 40px 20px;">
      <div style="max-width: 480px; margin: 0 auto; background: #ffffff; border-radius: 20px; padding: 40px; box-shadow: 0 4px 24px rgba(0,0,0,0.06);">
        <div style="text-align: center; margin-bottom: 32px;">
          <h1 style="font-size: 22px; font-weight: 800; color: #064e3b; margin: 0;">低碳价值筛选器</h1>
          <p style="font-size: 13px; color: #64748b; margin: 4px 0 0;">Low-Carbon Value Screener</p>
        </div>
        <p style="font-size: 15px; line-height: 1.6; color: #334155;">{greeting}</p>
        <p style="font-size: 15px; line-height: 1.6; color: #334155;">
          我们收到了重置密码的请求。请使用下面的验证码完成身份确认：
        </p>
        <div style="text-align: center; margin: 32px 0;">
          <div style="display: inline-block; background: linear-gradient(135deg, #059669, #047857); color: #ffffff; font-size: 32px; font-weight: 800; letter-spacing: 8px; padding: 20px 36px; border-radius: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;">
            {code}
          </div>
        </div>
        <p style="font-size: 14px; line-height: 1.6; color: #64748b; text-align: center;">
          验证码将在 <strong>15 分钟</strong>后失效。
        </p>
        <p style="font-size: 13px; line-height: 1.6; color: #94a3b8; margin-top: 32px; text-align: center;">
          如果不是你本人操作，请忽略这封邮件。
        </p>
      </div>
    </body>
    </html>
    """
    text = f"""{greeting}

我们收到了重置密码的请求。

验证码：{code}

验证码将在 15 分钟后失效。

如果不是你本人操作，请忽略这封邮件。
"""
    return text, html


def send_reset_email(to_email: str, code: str, user_name: str | None = None) -> bool:
    """Send a 6-digit reset code. Returns True if delivered or logged as sent."""
    from flask import current_app

    api_key = (current_app.config.get("RESEND_API_KEY") or "").strip()
    from_addr = (current_app.config.get("EMAIL_FROM") or "").strip() or DEFAULT_FROM
    smtp_host = (current_app.config.get("SMTP_HOST") or "").strip()

    text_body, html_body = _bodies(code, user_name)
    subject = "低碳价值筛选器 · 密码重置验证码"

    if api_key:
        try:
            resp = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": from_addr,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                },
                timeout=10,
            )
            if resp.status_code >= 400:
                logger.warning("Resend error %s: %s", resp.status_code, resp.text)
                return False
            return True
        except Exception:
            logger.exception("Resend request failed for %s", to_email)
            return False

    if smtp_host:
        return _send_smtp(to_email, from_addr, subject, text_body, html_body)

    logger.warning(
        "[DEV] Email not configured — password reset code for %s: %s",
        to_email,
        code,
    )
    return True


def _send_smtp(
    to_email: str,
    from_addr: str,
    subject: str,
    text_body: str,
    html_body: str,
) -> bool:
    from flask import current_app

    host = current_app.config.get("SMTP_HOST")
    port = int(current_app.config.get("SMTP_PORT") or 587)
    user = current_app.config.get("SMTP_USER") or ""
    password = current_app.config.get("SMTP_PASSWORD") or ""
    use_tls = str(current_app.config.get("SMTP_USE_TLS") or "true").lower() == "true"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.sendmail(from_addr, [to_email], msg.as_string())
        return True
    except Exception:
        logger.exception("SMTP send failed for %s", to_email)
        return False


def email_configured() -> bool:
    from flask import current_app

    return bool(
        (current_app.config.get("RESEND_API_KEY") or "").strip()
        or (current_app.config.get("SMTP_HOST") or "").strip()
    )
