"""Authentication API routes.

Endpoints:
  POST /api/auth/register — create a new account → token + user
  POST /api/auth/login    — email + password → token
  GET  /api/auth/me       — current user info (requires token in Authorization header)
  POST /api/auth/forgot-password — email a 6-digit reset code
  POST /api/auth/verify-reset-code
  POST /api/auth/reset-password
"""

import re
import logging

from flask import Blueprint, current_app, request, jsonify

from app.extensions import db
from app.models.password_reset import PasswordResetCode
from app.models.user import User
from app.services.email_service import (
    email_configured,
    generate_reset_code,
    send_reset_email,
)
from app.utils.auth import generate_token, login_required, current_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Basic email format check — good enough for MVP without pulling in a
# full validation library.  The UNIQUE constraint on the column is the
# real safety net against duplicates.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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


_GENERIC_RESET_MSG = "If that email is registered, a code has been sent"


@auth_bp.route("/auth/forgot-password", methods=["POST"])
def forgot_password():
    """Email a 6-digit code. Same success copy whether the account exists."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address"}), 400

    user = User.query.filter_by(email=email).first()
    if user is None:
        return jsonify({"message": _GENERIC_RESET_MSG})

    code = generate_reset_code()
    PasswordResetCode.issue(email, code)

    sent = send_reset_email(email, code, user.name)
    payload = {"message": _GENERIC_RESET_MSG}

    if not sent:
        if email_configured():
            return jsonify({"error": "Failed to send the code. Please try again."}), 502
        payload["message"] = "Email is not configured. Use the returned code to finish reset."

    # Classroom / local: expose the code when mail is not configured or DEBUG.
    if current_app.debug or not email_configured():
        payload["dev_code"] = code

    return jsonify(payload)


@auth_bp.route("/auth/verify-reset-code", methods=["POST"])
def verify_reset_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"error": "Enter email and code"}), 400

    if PasswordResetCode.find_valid(email, code) is None:
        return jsonify({"error": "Invalid or expired code"}), 400

    return jsonify({"message": "Code verified"})


@auth_bp.route("/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()
    new_password = data.get("new_password") or data.get("newPassword") or ""

    if not email or not code or not new_password:
        return jsonify({"error": "Email, code, and new password are required"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if len(new_password) > 128:
        return jsonify({"error": "Password must be 128 characters or fewer"}), 400

    row = PasswordResetCode.find_valid(email, code)
    if row is None:
        return jsonify({"error": "Invalid or expired code"}), 400

    user = User.query.filter_by(email=email).first()
    if user is None:
        return jsonify({"error": "Account not found"}), 404

    user.set_password(new_password)
    row.used = True
    db.session.commit()

    logger.info("Password reset for %s", email)
    return jsonify({"message": "Password updated. Sign in with your new password."})
