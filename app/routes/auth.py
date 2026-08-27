"""Authentication API routes.

Endpoints:
  POST /api/auth/register — create a new account → token + user
  POST /api/auth/login    — email + password → token
  GET  /api/auth/me       — current user info (requires token in Authorization header)
"""

import re
import logging

from flask import Blueprint, request, jsonify

from app.extensions import db
from app.models.user import User
from app.utils.auth import generate_token, verify_token, login_required, current_user

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
        return jsonify({"error": "请输入用户名"}), 400
    if len(name) > 100:
        return jsonify({"error": "用户名不能超过 100 个字符"}), 400
    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "请输入有效的邮箱地址"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少 6 位"}), 400
    if len(password) > 128:
        return jsonify({"error": "密码不能超过 128 个字符"}), 400

    # --- duplicate check --------------------------------------------------
    if User.query.filter_by(email=email).first() is not None:
        return jsonify({"error": "该邮箱已注册"}), 409

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

        {"error": "邮箱或密码错误"}
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "邮箱或密码错误"}), 401

    user = User.query.filter_by(email=email).first()

    # check_password handles the "user not found" case gracefully —
    # but we guard it to avoid calling check_password on None.
    if user is None or not user.check_password(password):
        return jsonify({"error": "邮箱或密码错误"}), 401

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
