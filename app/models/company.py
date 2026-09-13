"""Company model — static company identity only.

Maps to the ``companies`` table described in the PRD.  Each row represents a
single publicly-traded company identified by its ticker symbol.

**No market data lives here.**  Price, volume, market cap and every valuation
ratio are owned by ``financial_metrics`` (one snapshot row per company).  The
old duplicated ``companies.market_cap`` column has been removed so the two
tables cannot drift apart again.
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
    # NOTE: market_cap used to live here too. It is now owned exclusively by
    # financial_metrics.market_cap — do not add a market figure back to this
    # table (see docs/schema.md).
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
            "isin": self.isin,
        }

    def __repr__(self):
        return f"<Company {self.symbol}>"
