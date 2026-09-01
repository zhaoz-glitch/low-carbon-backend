"""Data sync job log — market (daily) and carbon (annual) pipeline runs."""

from datetime import datetime, timezone
from app.extensions import db


class DataSyncLog(db.Model):
    __tablename__ = "data_sync_logs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    job_name = db.Column(db.String(50), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False)  # success | failed | skipped
    source = db.Column(db.String(50))
    rows_upserted = db.Column(db.Integer, default=0)
    message = db.Column(db.Text)
    started_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    finished_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "job_name": self.job_name,
            "status": self.status,
            "source": self.source,
            "rows_upserted": self.rows_upserted,
            "message": self.message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def __repr__(self):
        return f"<DataSyncLog {self.job_name} {self.status}>"
