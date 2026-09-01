"""Password reset verification code model.

One row per issued code.  Codes are single-use and short-lived; a row is
marked ``used`` either when it is redeemed or when a newer code is issued
for the same account (so an old code can never be replayed after a reset).
"""

from datetime import datetime, timezone

from app.extensions import db


def utcnow() -> datetime:
    """Naive UTC now — MySQL DATETIME columns are timezone-naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PasswordResetCode(db.Model):
    __tablename__ = "password_reset_codes"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(8), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    def is_expired(self) -> bool:
        return self.expires_at < utcnow()

    def __repr__(self):
        return f"<PasswordResetCode {self.email} used={self.used}>"
