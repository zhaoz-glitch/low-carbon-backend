"""One-time 6-digit codes for password reset."""

from datetime import datetime, timedelta, timezone

from app.extensions import db


CODE_TTL_MINUTES = 15


class PasswordResetCode(db.Model):
    __tablename__ = "password_reset_codes"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    @staticmethod
    def issue(email: str, code: str) -> "PasswordResetCode":
        """Invalidate unused codes for this email, then store a new one."""
        PasswordResetCode.query.filter_by(email=email, used=False).update(
            {"used": True}
        )
        row = PasswordResetCode(
            email=email,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES),
        )
        db.session.add(row)
        db.session.commit()
        return row

    @staticmethod
    def find_valid(email: str, code: str) -> "PasswordResetCode | None":
        row = PasswordResetCode.query.filter_by(
            email=email, code=code, used=False
        ).order_by(PasswordResetCode.created_at.desc()).first()
        if row is None:
            return None
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return None
        return row
