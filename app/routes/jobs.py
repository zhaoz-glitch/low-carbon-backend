"""Manual data-sync endpoints (login required)."""

import logging

from flask import Blueprint, jsonify, request

from app.jobs.sync import latest_sync, sync_carbon, sync_market
from app.utils.auth import login_required

logger = logging.getLogger(__name__)

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/jobs/status", methods=["GET"])
@login_required
def job_status():
    """Latest market and carbon pipeline runs."""
    return jsonify({
        "market": latest_sync("market"),
        "carbon": latest_sync("carbon"),
    })


@jobs_bp.route("/jobs/sync-market", methods=["POST"])
@login_required
def trigger_market():
    """Fetch TradingView quotes/fundamentals and write today's row to the DB."""
    symbols = None
    body = request.get_json(silent=True) or {}
    if isinstance(body.get("symbols"), list):
        symbols = [str(s).upper() for s in body["symbols"]]
    result = sync_market(symbols=symbols, reason="http")
    status = 200 if result.get("status") in ("success", "skipped") else 502
    return jsonify(result), status


@jobs_bp.route("/jobs/sync-carbon", methods=["POST"])
@login_required
def trigger_carbon():
    """Fetch Clarity AI SFDR carbon metrics (requires API key + secret)."""
    symbols = None
    body = request.get_json(silent=True) or {}
    if isinstance(body.get("symbols"), list):
        symbols = [str(s).upper() for s in body["symbols"]]
    result = sync_carbon(symbols=symbols, reason="http")
    status = 200 if result.get("status") in ("success", "skipped") else 502
    return jsonify(result), status
