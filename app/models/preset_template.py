"""Preset template model — pre-configured filter combinations.

The PRD defines four preset templates that lower the usage barrier for users:
  1. Low-Carbon Value Trap      — PE < 15 & carbon intensity YoY down > 5%
  2. Green High Growth          — revenue growth > 20% & carbon intensity < 50% of industry avg
  3. Net Zero Pioneer           — carbon intensity YoY down > 15% & absolute emissions < 5M t
  4. High Dividend Green        — dividend yield > 3% & carbon intensity < 200 tCO2e/$M
"""

from datetime import datetime, timezone
from app.extensions import db


class PresetTemplate(db.Model):
    __tablename__ = "preset_templates"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    use_case = db.Column(db.String(300))
    # JSON blob storing the filter conditions
    filters = db.Column(db.JSON, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "use_case": self.use_case,
            "filters": self.filters,
            "is_active": self.is_active,
        }

    def __repr__(self):
        return f"<PresetTemplate {self.name}>"
