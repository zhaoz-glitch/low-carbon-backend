"""Financial snapshot model — the single source of truth for market data.

**Exactly one row per company.**  ``financial_metrics`` holds the *current*
market state (price, volume, market cap, valuation ratios, margin, revenue);
``companies`` holds static identity data only.  Nothing else may store a
market figure — this is what keeps the two tables from drifting apart.

Two dates, two different meanings:

    as_of_date   the trading day the snapshot describes (upstream data date)
    updated_at   when this row was last written by an ETL / live-quote run

Responsibility map
------------------
    companies          static identity   symbol, name, sector, industry,
                                         exchange, isin
    financial_metrics  current snapshot  price / volume / cap / ratios / margin
    carbon_emissions   annual series     one row per (symbol, report_year)

Writers must go through ``app.services.market_snapshot_service`` so that a
partial upstream payload can never blank out fields a richer payload filled.
"""

from datetime import datetime, timezone

from app.extensions import db


class FinancialMetric(db.Model):
    __tablename__ = "financial_metrics"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(
        db.String(10),
        db.ForeignKey("companies.symbol", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Trading day this snapshot describes (NOT the row write time).
    as_of_date = db.Column(db.Date, index=True)

    # Market data
    close = db.Column(db.Numeric(12, 4))  # closing price
    volume = db.Column(db.Numeric(20, 0))  # daily volume (shares)
    market_cap = db.Column(db.Numeric(20, 2))  # market capitalization (USD)

    # Valuation ratios
    pe_ttm = db.Column(db.Numeric(10, 2))  # P/E ratio (TTM)
    pb = db.Column(db.Numeric(10, 2))  # P/B ratio
    dividend_yield = db.Column(db.Numeric(8, 4))  # as percentage, e.g. 2.5 = 2.5%

    # Trading indicators
    turnover = db.Column(db.Numeric(8, 2))  # turnover rate (%), volume / float
    week_52_change = db.Column(db.Numeric(10, 2))  # 52-week price change (%)

    # Fundamentals
    net_profit_margin = db.Column(db.Numeric(10, 2))  # net profit margin (%)
    revenue_growth = db.Column(db.Numeric(10, 2))  # YoY revenue growth (%)
    revenue = db.Column(db.Numeric(20, 2))  # annual revenue (USD, TTM)

    data_source = db.Column(db.String(50), default="tradingview")

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # One snapshot per company — no dated history rows.
    __table_args__ = (
        db.UniqueConstraint("symbol", name="uq_financial_metrics_symbol"),
    )

    def to_dict(self):
        return {
            "symbol": self.symbol,
            # Kept as "date" for API compatibility with the frontend contract.
            "date": self.as_of_date.isoformat() if self.as_of_date else None,
            "close": float(self.close) if self.close is not None else None,
            "volume": float(self.volume) if self.volume is not None else None,
            "market_cap": float(self.market_cap) if self.market_cap is not None else None,
            "pe_ttm": float(self.pe_ttm) if self.pe_ttm is not None else None,
            "pb": float(self.pb) if self.pb is not None else None,
            "dividend_yield": (
                float(self.dividend_yield) if self.dividend_yield is not None else None
            ),
            "turnover": float(self.turnover) if self.turnover is not None else None,
            "week_52_change": (
                float(self.week_52_change) if self.week_52_change is not None else None
            ),
            "net_profit_margin": (
                float(self.net_profit_margin) if self.net_profit_margin is not None else None
            ),
            "revenue_growth": (
                float(self.revenue_growth) if self.revenue_growth is not None else None
            ),
            "revenue": float(self.revenue) if self.revenue is not None else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<FinancialMetric {self.symbol} as_of={self.as_of_date}>"
