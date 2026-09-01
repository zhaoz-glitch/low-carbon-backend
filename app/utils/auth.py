"""Authentication utilities — token generation, verification, and the
``@login_required`` decorator.

Tokens are generated with ``itsdangerous.URLSafeTimedSerializer`` which
produces signed, time-limited tokens without needing a database lookup
on every request.  The token payload is the user's integer ID.

Usage in a route::

    from app.utils.auth import login_required, current_user

    @screener_bp.route("/protected")
    @login_required
    def protected():
        user = current_user()  # the authenticated User object
        return jsonify(user.to_dict())
"""

from functools import wraps

from flask import current_app, g, request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.models.user import User


# --------------------------------------------------------------------------- #
#  Serializer helpers
# --------------------------------------------------------------------------- #

def _get_serializer() -> URLSafeTimedSerializer:
    """Build a serializer bound to the app's SECRET_KEY."""
    return URLSafeTimedSerializer(
        secret_key=current_app.config["SECRET_KEY"],
        salt="auth-token",
    )


def generate_token(user_id: int) -> str:
    """Create a signed token that encodes *user_id*."""
    return _get_serializer().dumps(user_id)


def verify_token(token: str, max_age: int = 7 * 24 * 3600) -> int | None:
    """Verify a token and return the user ID, or ``None`` if invalid / expired.

    Default expiry: 7 days.
    """
    try:
        user_id = _get_serializer().loads(token, max_age=max_age)
        return int(user_id)
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
#  Decorator
# --------------------------------------------------------------------------- #

def current_user() -> User | None:
    """Return the authenticated user for the current request, or ``None``.

    Only valid inside a route wrapped with ``@login_required``.
    """
    return getattr(g, "user", None)


def login_required(fn):
    """Decorator that enforces authentication.

    Expects the token in the ``Authorization`` header as a bare string
    (not a ``Bearer`` prefix).  If the token is missing, invalid, or
    expired, a ``401`` is returned.  On success the :class:`User` object
    is stored in ``flask.g.user`` and can be retrieved via
    :func:`current_user`.
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        # Accept both "Bearer <token>" and bare "<token>"
        token = auth_header
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]

        if not token:
            return {"error": "Not signed in"}, 401

        user_id = verify_token(token)
        if user_id is None:
            return {"error": "Session expired. Please sign in again."}, 401

        user = User.query.get(user_id)
        if user is None:
            return {"error": "User not found"}, 401

        g.user = user
        return fn(*args, **kwargs)

    return wrapper
