"""Carbon emission model — annual carbon data.

Maps to the ``carbon_emissions`` table in the PRD.  Carbon data comes from
annual reports (10-K) and is updated 1-2 times per year per company.  The PRD
emphasizes clearly labeling the report year and distinguishing "real-time
market data" from "annual carbon data".
"""

from datetime import datetime, timezone
from app.extensions import db


class CarbonEmission(db.Model):
    __tablename__ = "carbon_emissions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    symbol = db.Column(
        db.String(10),
        db.ForeignKey("companies.symbol", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_year = db.Column(db.Integer, nullable=False, index=True)

    # Absolute emissions (metric tons CO2 equivalent)
    scope1 = db.Column(db.Numeric(20, 2))  # direct emissions
    scope2 = db.Column(db.Numeric(20, 2))  # indirect (purchased energy)
    total_emissions = db.Column(db.Numeric(20, 2))  # scope1 + scope2

    # Carbon intensity (emissions / revenue) — tCO2e per $1M revenue
    carbon_intensity_revenue = db.Column(db.Numeric(12, 4))

    # Year-over-year change in carbon intensity (%)
    carbon_change_yoy = db.Column(db.Numeric(10, 2))

    # Financial context for the report year
    revenue = db.Column(db.Numeric(20, 2))  # annual revenue (USD)

    # Data source tracking
    data_source = db.Column(db.String(100), default="mock")
    has_carbon_data = db.Column(db.Boolean, default=True)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        db.UniqueConstraint("symbol", "report_year", name="uq_symbol_year"),
    )

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "report_year": self.report_year,
            "scope1": float(self.scope1) if self.scope1 else None,
            "scope2": float(self.scope2) if self.scope2 else None,
            "total_emissions": float(self.total_emissions) if self.total_emissions else None,
            "carbon_intensity_revenue": float(self.carbon_intensity_revenue)
            if self.carbon_intensity_revenue
            else None,
            "carbon_change_yoy": float(self.carbon_change_yoy)
            if self.carbon_change_yoy
            else None,
            "revenue": float(self.revenue) if self.revenue else None,
            "data_source": self.data_source,
            "has_carbon_data": self.has_carbon_data,
        }

    def __repr__(self):
        return f"<CarbonEmission {self.symbol} {self.report_year}>"
