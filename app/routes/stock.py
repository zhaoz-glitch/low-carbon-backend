"""Stock detail API routes.

Endpoints:
  GET /api/stock/<symbol>              — stock detail (company + financials + carbon)
  GET /api/stock/<symbol>/carbon-trend — 5-year carbon emission trend for charts
"""

import logging
from flask import Blueprint, jsonify

from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.models.carbon_emission import CarbonEmission
from app.utils.mock_data import get_carbon_trend

logger = logging.getLogger(__name__)

stock_bp = Blueprint("stock", __name__)


@stock_bp.route("/stock/<string:symbol>", methods=["GET"])
def get_stock_detail(symbol):
    """Return detailed information for a single stock.

    Used by the detail drawer in the frontend (PRD section 3.3):
      - Financial overview card (PE, PB, dividend yield, market cap)
      - Carbon emission history trend (5-year Scope 1+2)
      - Data source & report year annotation
    """
    symbol = symbol.upper().strip()

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

    # 5-year carbon history for the trend chart
    carbon_history = get_carbon_trend(symbol)

    return jsonify({
        "company": company.to_dict(),
        "financials": latest_fin.to_dict() if latest_fin else None,
        "carbon": latest_carbon.to_dict() if latest_carbon else None,
        "carbon_history": carbon_history,
    })


@stock_bp.route("/stock/<string:symbol>/carbon-trend", methods=["GET"])
def get_carbon_trend_endpoint(symbol):
    """Return 5-year carbon emission trend data for the detail drawer chart.

    Provides data for the recharts trend chart showing Scope 1+2 emissions
    and carbon intensity changes over the past 5 years.
    """
    symbol = symbol.upper().strip()

    company = Company.query.filter_by(symbol=symbol).first()
    if not company:
        return jsonify({"error": "Stock not found", "symbol": symbol}), 404

    trend_data = get_carbon_trend(symbol)

    return jsonify({
        "symbol": symbol,
        "name": company.name,
        "trend": trend_data,
    })
