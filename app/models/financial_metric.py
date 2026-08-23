"""Financial metric model — market & technical indicators.

Maps to the ``financial_metrics`` table in the PRD.  Data is refreshed daily
(during/after market close) and cached in Redis for 5 minutes for real-time
quotes.
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
    date = db.Column(db.Date, nullable=False, index=True)

    # Market data
    close = db.Column(db.Numeric(12, 4))  # closing price
    volume = db.Column(db.Numeric(20, 0))  # daily volume
    market_cap = db.Column(db.Numeric(20, 2))  # market capitalization (USD)

    # Valuation ratios
    pe_ttm = db.Column(db.Numeric(10, 2))  # P/E ratio (TTM)
    pb = db.Column(db.Numeric(10, 2))  # P/B ratio
    dividend_yield = db.Column(db.Numeric(8, 4))  # as percentage, e.g. 2.5 = 2.5%

    # Trading indicators
    turnover = db.Column(db.Numeric(8, 2))  # turnover ratio (%)
    week_52_change = db.Column(db.Numeric(10, 2))  # 52-week price change (%)

    # Fundamentals
    net_profit_margin = db.Column(db.Numeric(10, 2))  # net profit margin (%)
    revenue_growth = db.Column(db.Numeric(10, 2))  # YoY revenue growth (%)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Composite unique constraint: one record per symbol per day
    __table_args__ = (
        db.UniqueConstraint("symbol", "date", name="uq_symbol_date"),
    )

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "date": self.date.isoformat() if self.date else None,
            "close": float(self.close) if self.close else None,
            "volume": float(self.volume) if self.volume else None,
            "market_cap": float(self.market_cap) if self.market_cap else None,
            "pe_ttm": float(self.pe_ttm) if self.pe_ttm else None,
            "pb": float(self.pb) if self.pb else None,
            "dividend_yield": float(self.dividend_yield) if self.dividend_yield else None,
            "turnover": float(self.turnover) if self.turnover else None,
            "week_52_change": float(self.week_52_change) if self.week_52_change else None,
            "net_profit_margin": float(self.net_profit_margin) if self.net_profit_margin else None,
            "revenue_growth": float(self.revenue_growth) if self.revenue_growth else None,
        }

    def __repr__(self):
        return f"<FinancialMetric {self.symbol} {self.date}>"
