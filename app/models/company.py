"""Company model — basic stock info.

Maps to the ``companies`` table described in the PRD.  Each row represents a
single publicly-traded company identified by its ticker symbol.
"""

from datetime import datetime, timezone
from app.extensions import db


class Company(db.Model):
    __tablename__ = "companies"

    symbol = db.Column(db.String(10), primary_key=True)  # e.g. "AAPL"
    name = db.Column(db.String(200), nullable=False)
    sector = db.Column(db.String(100))  # e.g. "Technology"
    industry = db.Column(db.String(200))
    exchange = db.Column(db.String(50))  # e.g. "NASDAQ"
    market_cap = db.Column(db.Numeric(20, 2))  # in USD
    isin = db.Column(db.String(12), index=True)  # Clarity AI security id

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    financial_metrics = db.relationship(
        "FinancialMetric", backref="company", lazy="dynamic",
        cascade="all, delete-orphan"
    )
    carbon_emissions = db.relationship(
        "CarbonEmission", backref="company", lazy="dynamic",
        cascade="all, delete-orphan"
    )

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "name": self.name,
            "sector": self.sector,
            "industry": self.industry,
            "exchange": self.exchange,
            "market_cap": float(self.market_cap) if self.market_cap else None,
            "isin": self.isin,
        }

    def __repr__(self):
        return f"<Company {self.symbol}>"
