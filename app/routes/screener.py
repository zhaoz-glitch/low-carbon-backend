"""Screener API routes.

Endpoints (per PRD section 4):
  GET  /api/screener/fields      — filter field metadata for dynamic form
  POST /api/screener/run         — execute screening (core)
  POST /api/screener/export      — export results as CSV
  GET  /api/screener/templates   — list preset filter templates
  GET  /api/screener/templates/:id — get a single preset template
"""

import logging
from flask import Blueprint, request, jsonify, Response, current_app

from app.services.tradingview_service import tradingview_service
from app.services.carbon_service import carbon_service
from app.services.screener_service import screener_service
from app.utils.csv_export import generate_csv
from app.utils.auth import login_required
from app.models.preset_template import PresetTemplate
from app.extensions import db

logger = logging.getLogger(__name__)

screener_bp = Blueprint("screener", __name__)


@screener_bp.route("/screener/fields", methods=["GET"])
@login_required
def get_fields():
    """Return all filter field metadata for the frontend dynamic form.

    Combines Dimension A (market/technical, real-time/daily) and
    Dimension B (green/carbon, annual) as described in PRD section 3.2.
    """
    market_fields = tradingview_service.get_market_fields_metadata()
    carbon_fields = carbon_service.get_carbon_fields_metadata()

    return jsonify({
        "dimensions": [
            {
                "key": "market",
                "label": "维度A：市场与技术面",
                "update_frequency": "实时 / 日更",
                "fields": market_fields,
            },
            {
                "key": "carbon",
                "label": "维度B：绿色 / 碳排指标",
                "update_frequency": "年度更新",
                "fields": carbon_fields,
            },
        ],
    })


@screener_bp.route("/screener/run", methods=["POST"])
@login_required
def run_screener():
    """Execute the screening pipeline (core endpoint).

    Request body (per PRD section 4.2):
    {
        "filters": {
            "market_cap_basic": {"min": 1000000000, "max": 100000000000},
            "price_earnings_ttm": {"max": 15},
            "turnover": {"min": 5},
            "carbon_intensity_revenue": {"max": 100},
            "carbon_change_yoy": {"max": -5},
            "has_carbon_data": "true"
        },
        "page": 1,
        "pageSize": 50,
        "sortBy": "market_cap_basic",
        "sortOrder": "desc"
    }
    """
    data = request.get_json(silent=True) or {}
    filters = data.get("filters", {})

    # Pagination
    page = max(1, data.get("page", 1))
    page_size = min(
        max(1, data.get("pageSize", 50)),
        current_app.config.get("MAX_PAGE_SIZE", 200),
    )

    # Sorting
    sort_by = data.get("sortBy", "market_cap_basic")
    sort_order = data.get("sortOrder", "desc")
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    try:
        result = screener_service.run_screener(
            filters=filters,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return jsonify(result)
    except Exception as e:
        logger.error("Screener run failed: %s", e, exc_info=True)
        return jsonify({"error": "Screening failed", "message": str(e)}), 500


@screener_bp.route("/screener/export", methods=["POST"])
@login_required
def export_screener():
    """Export screening results as a CSV file.

    Reuses the same filter logic as /screener/run but returns a CSV
    download instead of JSON. All matching results are exported (no
    pagination limit for the file).
    """
    data = request.get_json(silent=True) or {}
    filters = data.get("filters", {})
    sort_by = data.get("sortBy", "market_cap_basic")
    sort_order = data.get("sortOrder", "desc")

    try:
        # Fetch all results (large page size for export)
        result = screener_service.run_screener(
            filters=filters,
            page=1,
            page_size=10000,  # export all
            sort_by=sort_by,
            sort_order=sort_order,
        )

        csv_content = generate_csv(result["data"])

        return Response(
            csv_content,
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=low_carbon_screener_export.csv",
            },
        )
    except Exception as e:
        logger.error("CSV export failed: %s", e, exc_info=True)
        return jsonify({"error": "Export failed", "message": str(e)}), 500


@screener_bp.route("/screener/templates", methods=["GET"])
@login_required
def get_templates():
    """List all preset filter templates (PRD section 3.2)."""
    templates = PresetTemplate.query.filter_by(is_active=True).all()
    return jsonify({
        "templates": [t.to_dict() for t in templates],
    })


@screener_bp.route("/screener/templates/<int:template_id>", methods=["GET"])
@login_required
def get_template(template_id):
    """Get a single preset template by ID."""
    tpl = PresetTemplate.query.get_or_404(template_id)
    return jsonify(tpl.to_dict())
