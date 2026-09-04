"""Stock detail API routes.

Endpoints:
  GET /api/stock/<symbol>              — stock detail (company + financials + carbon)
  GET /api/stock/<symbol>/carbon-trend — 5-year carbon emission trend for charts
"""

import logging
import urllib.parse
from flask import Blueprint, jsonify
from sqlalchemy import func

from app.extensions import db
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.models.carbon_emission import CarbonEmission
from app.utils.auth import login_required
from app.utils.mock_data import get_carbon_trend

logger = logging.getLogger(__name__)

stock_bp = Blueprint("stock", __name__)


def _attach_carbon_baselines(symbol, history):
    """Attach per-year US-market & sector-average baselines to a trend series.

    Each baseline is the *cross-sectional simple average* of
    ``carbon_intensity_revenue`` for the report year, computed over every
    company that actually has a value that year.  Companies whose intensity
    is NULL never enter the statistic — they are filtered out rather than
    counted as zero (avoiding downward bias from non-disclosers).

    Mutates each point dict with:
      - us_avg_intensity / us_peer_count      (whole covered US universe)
      - sector_avg_intensity / sector_peer_count  (same-year, company's sector)

    Any year without peers (or a company without a sector) yields NULL so the
    frontend can simply skip that point on the reference line.
    """
    if not history:
        return history

    company = Company.query.filter_by(symbol=symbol).first()
    years = [p["report_year"] for p in history]

    def _agg(extra_filter):
        rows = (
            db.session.query(
                CarbonEmission.report_year,
                func.avg(CarbonEmission.carbon_intensity_revenue),
                func.count(CarbonEmission.carbon_intensity_revenue),
            )
            .join(Company, Company.symbol == CarbonEmission.symbol)
            .filter(
                CarbonEmission.report_year.in_(years),
                CarbonEmission.carbon_intensity_revenue.isnot(None),
            )
        )
        if extra_filter is not None:
            rows = rows.filter(extra_filter)
        return {
            y: (float(a) if a is not None else None, n)
            for y, a, n in rows.group_by(CarbonEmission.report_year).all()
        }

    market = _agg(None)
    sector = _agg(Company.sector == company.sector) if company and company.sector else {}

    for point in history:
        y = point["report_year"]
        m, s = market.get(y), sector.get(y)
        point["us_avg_intensity"] = round(m[0], 4) if m and m[0] is not None else None
        point["us_peer_count"] = m[1] if m else None
        point["sector_avg_intensity"] = round(s[0], 4) if s and s[0] is not None else None
        point["sector_peer_count"] = s[1] if s else None
    return history


@stock_bp.route("/stock/<string:symbol>", methods=["GET"])
@login_required
def get_stock_detail(symbol):
    """Return detailed information for a single stock.

    Used by the detail drawer in the frontend (PRD section 3.3):
      - Financial overview card (PE, PB, dividend yield, market cap)
      - Carbon emission history trend (5-year Scope 1+2)
      - Data source & report year annotation
    """
    # The frontend double-encodes slashes so that %2F is not decoded by
    # upstream proxies into a path separator.  Unquote once here to restore
    # the original symbol, e.g. "BML/PJ".
    symbol = urllib.parse.unquote(symbol).upper().strip()

    company = Company.query.filter_by(symbol=symbol).first()
    if not company:
        return jsonify({"error": "Stock not found", "symbol": symbol}), 404

    # Latest financial metrics
    latest_fin = (
        FinancialMetric.query
        .filter_by(symbol=symbol)
        .order_by(FinancialMetric.date.desc())
        .first()
    )

    # Latest carbon emission data
    latest_carbon = (
        CarbonEmission.query
        .filter_by(symbol=symbol)
        .order_by(CarbonEmission.report_year.desc())
        .first()
    )

    # 5-year carbon history for the trend chart (+ US/sector baselines)
    carbon_history = _attach_carbon_baselines(symbol, get_carbon_trend(symbol))

    return jsonify({
        "company": company.to_dict(),
        "financials": latest_fin.to_dict() if latest_fin else None,
        "carbon": latest_carbon.to_dict() if latest_carbon else None,
        "carbon_history": carbon_history,
    })


@stock_bp.route("/stock/<string:symbol>/carbon-trend", methods=["GET"])
@login_required
def get_carbon_trend_endpoint(symbol):
    """Return 5-year carbon emission trend data for the detail drawer chart.

    Provides data for the recharts trend chart showing Scope 1+2 emissions
    and carbon intensity changes over the past 5 years.
    """
    symbol = urllib.parse.unquote(symbol).upper().strip()

    company = Company.query.filter_by(symbol=symbol).first()
    if not company:
        return jsonify({"error": "Stock not found", "symbol": symbol}), 404

    trend_data = _attach_carbon_baselines(symbol, get_carbon_trend(symbol))

    return jsonify({
        "symbol": symbol,
        "name": company.name,
        "trend": trend_data,
    })
