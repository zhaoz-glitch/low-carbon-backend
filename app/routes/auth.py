"""Authentication API routes.

Endpoints:
  POST /api/auth/register — create a new account → token + user
  POST /api/auth/login    — email + password → token
  GET  /api/auth/me       — current user info (requires token in Authorization header)
  POST /api/auth/forgot-password — email → send 6-digit reset code
  POST /api/auth/verify-reset-code — email + code → validate code without resetting
  POST /api/auth/reset-password  — email + code + new password → reset
"""

import re
import secrets
import logging
from datetime import timedelta

from flask import Blueprint, current_app, request, jsonify

from app.extensions import db
from app.models.user import User
from app.models.password_reset_code import PasswordResetCode, utcnow
from app.services.mail import send_password_reset_code
from app.utils.auth import generate_token, verify_token, login_required, current_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Basic email format check — good enough for MVP without pulling in a
# full validation library.  The UNIQUE constraint on the column is the
# real safety net against duplicates.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Reset codes expire after this long and are single-use.
_CODE_TTL = timedelta(minutes=15)
# Anti-spam: one code per email per window.
_RESEND_WINDOW = timedelta(seconds=60)


@auth_bp.route("/auth/register", methods=["POST"])
def register():
    """Create a new user account and return a token (auto-login).

    Request body::

        {"email": "user@example.com", "password": "secret", "name": "Alice"}

    On success returns 201 with the same shape as login::

        {"token": "<signed-token>", "user": { id, email, name, ... }}

    Validation errors return 400 with a descriptive message so the
    frontend can show field-specific feedback.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = (data.get("name") or "").strip()
    password = data.get("password") or ""

    # --- validation -------------------------------------------------------
    if not name:
        return jsonify({"error": "Enter a name"}), 400
    if len(name) > 100:
        return jsonify({"error": "Name must be 100 characters or fewer"}), 400
    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if len(password) > 128:
        return jsonify({"error": "Password must be 128 characters or fewer"}), 400

    # --- duplicate check --------------------------------------------------
    if User.query.filter_by(email=email).first() is not None:
        return jsonify({"error": "This email is already registered"}), 409

    # --- create user ------------------------------------------------------
    user = User(email=email, name=name)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    logger.info("New user registered: %s (id=%s)", email, user.id)

    token = generate_token(user.id)
    return jsonify({"token": token, "user": user.to_dict()}), 201


@auth_bp.route("/auth/login", methods=["POST"])
def login():
    """Authenticate a user and return a token.

    Request body::

        {"email": "user@example.com", "password": "secret"}

    On success returns::

        {"token": "<signed-token>", "user": { id, email, name, ... }}

    On failure (wrong email, wrong password, missing fields) always
    returns the same message to avoid leaking which part was wrong::

        {"error": "Invalid email or password"}
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Invalid email or password"}), 401

    user = User.query.filter_by(email=email).first()

    # check_password handles the "user not found" case gracefully —
    # but we guard it to avoid calling check_password on None.
    if user is None or not user.check_password(password):
        return jsonify({"error": "Invalid email or password"}), 401

    token = generate_token(user.id)
    return jsonify({"token": token, "user": user.to_dict()})


@auth_bp.route("/auth/me", methods=["GET"])
@login_required
def get_me():
    """Return the authenticated user's profile.

    Requires a valid token in the ``Authorization`` header.  The
    ``@login_required`` decorator handles the 401 responses; if we
    reach this point the user is guaranteed to be authenticated.
    """
    user = current_user()
    return jsonify({"user": user.to_dict()})


def _generate_code() -> str:
    """6-digit numeric code (100000–999999)."""
    return f"{secrets.randbelow(1_000_000):06d}"


@auth_bp.route("/auth/forgot-password", methods=["POST"])
def forgot_password():
    """Send a 6-digit reset code to the user's email.

    Request body::

        {"email": "user@example.com"}

    Always returns 200 for a well-formed request even when the account does
    not exist, so callers can't enumerate registered emails.  A 429 is
    returned if a code was already issued within the last 60 seconds.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "请输入有效的邮箱地址"}), 400

    user = User.query.filter_by(email=email).first()

    # Rate limit regardless of account existence (cheap anti-spam).
    recent = (
        PasswordResetCode.query.filter_by(email=email)
        .order_by(PasswordResetCode.id.desc())
        .first()
    )
    if recent and recent.created_at and utcnow() - recent.created_at < _RESEND_WINDOW:
        return jsonify({"error": "发送太频繁，请 60 秒后再试"}), 429

    if user is None:
        # Identical response — don't reveal whether the email is registered.
        logger.info("Forgot-password request for unknown email %s", email)
        return jsonify({"message": "如果该邮箱已注册，验证码已发送到你的邮箱"}), 200

    # Invalidate any previously issued codes for this account, then issue one.
    PasswordResetCode.query.filter_by(email=email).update({"used": True})
    code = _generate_code()
    db.session.add(
        PasswordResetCode(
            email=email,
            code=code,
            expires_at=utcnow() + _CODE_TTL,
        )
    )
    db.session.commit()

    send_password_reset_code(email, code)
    logger.info("Password reset code issued for %s", email)
    return jsonify({"message": "验证码已发送到你的邮箱"}), 200


@auth_bp.route("/auth/verify-reset-code", methods=["POST"])
def verify_reset_code():
    """Validate a reset code without actually resetting the password.

    Request body::

        {"email": "user@example.com", "code": "123456"}

    Returns 200 if the code is valid and not expired, 400 otherwise.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "请输入有效的邮箱地址"}), 400
    if not code:
        return jsonify({"error": "请输入验证码"}), 400

    user = User.query.filter_by(email=email).first()
    if user is None:
        return jsonify({"error": "验证码无效或已过期"}), 400

    record = (
        PasswordResetCode.query.filter_by(email=email, code=code, used=False)
        .order_by(PasswordResetCode.id.desc())
        .first()
    )
    if record is None or record.is_expired():
        return jsonify({"error": "验证码无效或已过期"}), 400

    return jsonify({"message": "验证码有效"}), 200


@auth_bp.route("/auth/reset-password", methods=["POST"])
def reset_password():
    """Redeem a reset code and set a new password.

    Request body::

        {"email": "user@example.com", "code": "123456", "new_password": "new-secret"}

    ``new_password`` is preferred; ``password`` is accepted for backward
    compatibility.

    The code is single-use: redeeming it invalidates every outstanding code
    for the account, and after success the user logs in with the new
    password.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    password = data.get("new_password") or data.get("password") or ""

    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "请输入有效的邮箱地址"}), 400
    if not code:
        return jsonify({"error": "请输入验证码"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if len(password) > 128:
        return jsonify({"error": "密码不能超过 128 个字符"}), 400

    user = User.query.filter_by(email=email).first()
    if user is None:
        return jsonify({"error": "验证码无效或已过期"}), 400

    record = (
        PasswordResetCode.query.filter_by(email=email, code=code, used=False)
        .order_by(PasswordResetCode.id.desc())
        .first()
    )
    if record is None or record.is_expired():
        return jsonify({"error": "验证码无效或已过期"}), 400

    # Single-use: burn every outstanding code for the account.
    PasswordResetCode.query.filter_by(email=email).update({"used": True})
    user.set_password(password)
    db.session.commit()

    logger.info("Password reset completed for %s", email)
    return jsonify({"message": "密码已重置，请使用新密码登录"}), 200
