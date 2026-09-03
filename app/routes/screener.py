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
from app.utils.filter_validation import validate_filters, template_filters_to_api
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
                "label": "A · Market & Technicals",
                "update_frequency": "Real-time / Daily",
                "fields": market_fields,
            },
            {
                "key": "carbon",
                "label": "B · Green / Carbon",
                "update_frequency": "Annual",
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
    raw_filters = data.get("filters", {})

    # Validate and normalize numeric inputs (reject "abc", unify "10%"/"0.1").
    try:
        filters = validate_filters(raw_filters)
    except ValueError as e:
        return jsonify({"error": "Invalid filter value", "message": str(e)}), 400

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
        from app.jobs.sync import maybe_refresh_live_quotes

        maybe_refresh_live_quotes()
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
    """Export screening results as CSV (or ZIP with charts).

    Reuses the same filter logic as /screener/run but returns a file
    download instead of JSON.  All matching results are exported (no
    pagination limit).

    Optional body fields:
      - symbols: list of specific symbols to export (filters applied first)
      - includeCharts: bool — when true, generates 5-year carbon trend
        PNGs for each exported stock and returns a ZIP archive.
    """
    import io
    import zipfile
    import tempfile
    from pathlib import Path

    from app.utils.chart_export import generate_carbon_trend_chart

    data = request.get_json(silent=True) or {}
    raw_filters = data.get("filters", {})
    symbols = data.get("symbols")
    include_charts = bool(data.get("includeCharts"))

    try:
        filters = validate_filters(raw_filters)
    except ValueError as e:
        return jsonify({"error": "Invalid filter value", "message": str(e)}), 400

    sort_by = data.get("sortBy", "market_cap_basic")
    sort_order = data.get("sortOrder", "desc")

    try:
        # Fetch all results (large page size for export)
        result = screener_service.run_screener(
            filters=filters,
            page=1,
            page_size=10000,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        rows = result["data"]

        # If explicit symbol list provided, filter to those symbols
        if symbols and isinstance(symbols, list):
            symbol_set = {s.upper().strip() for s in symbols if isinstance(s, str)}
            rows = [r for r in rows if r.get("symbol", "").upper() in symbol_set]

        if not rows:
            return jsonify({"error": "No matching stocks to export"}), 400

        csv_content = generate_csv(rows)

        if not include_charts:
            return Response(
                csv_content,
                mimetype="text/csv",
                headers={
                    "Content-Disposition": "attachment; filename=low_carbon_screener_export.csv",
                },
            )

        # Generate ZIP with CSV + chart PNGs
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            csv_path = tmp / "screener_export.csv"
            csv_path.write_text(csv_content, encoding="utf-8")

            chart_dir = tmp / "charts"
            chart_dir.mkdir()

            chart_count = 0
            for row in rows:
                sym = row.get("symbol")
                if sym and generate_carbon_trend_chart(sym, chart_dir):
                    chart_count += 1

            pngs = sorted(chart_dir.glob("*.png"))
            if not pngs:
                logger.warning(
                    "Export requested charts but none generated (%d rows)", len(rows)
                )
                return (
                    jsonify({
                        "error": "Chart generation failed",
                        "message": (
                            "No 5-year carbon trend data found for the selected stocks. "
                            "Export the CSV without charts instead."
                        ),
                    }),
                    502,
                )

            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(csv_path, arcname="screener_export.csv")
                for png in pngs:
                    zf.write(png, arcname=f"charts/{png.name}")

            zip_buffer.seek(0)
            return Response(
                zip_buffer.getvalue(),
                mimetype="application/zip",
                headers={
                    "Content-Disposition": "attachment; filename=low_carbon_screener_export.zip",
                },
            )
    except Exception as e:
        logger.error("Export failed: %s", e, exc_info=True)
        return jsonify({"error": "Export failed", "message": str(e)}), 500


def _serialize_template(tpl: PresetTemplate) -> dict:
    """Serve template filters in the API's fraction convention for percent fields."""
    d = tpl.to_dict()
    d["filters"] = template_filters_to_api(d.get("filters"))
    return d


@screener_bp.route("/screener/templates", methods=["GET"])
@login_required
def get_templates():
    """List all preset filter templates (PRD section 3.2)."""
    templates = PresetTemplate.query.filter_by(is_active=True).all()
    return jsonify({
        "templates": [_serialize_template(t) for t in templates],
    })


@screener_bp.route("/screener/templates/<int:template_id>", methods=["GET"])
@login_required
def get_template(template_id):
    """Get a single preset template by ID."""
    tpl = PresetTemplate.query.get_or_404(template_id)
    return jsonify(_serialize_template(tpl))
