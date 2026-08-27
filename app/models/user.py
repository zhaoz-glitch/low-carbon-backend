"""User model — authentication and identity.

Stores a password hash (never the plaintext password).  The ``to_dict``
method deliberately omits the hash so it can never leak to the frontend.
"""

from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # -- password helpers -------------------------------------------------
    def set_password(self, raw: str) -> None:
        """Hash and store the password (never save raw text)."""
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        """Verify a plaintext candidate against the stored hash."""
        return check_password_hash(self.password_hash, raw)

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict:
        """Return a JSON-safe dict.

        ``password_hash`` is intentionally excluded so it can never be
        sent to the frontend, even by accident.
        """
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<User {self.email}>"
