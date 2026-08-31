"""Admin endpoints — guarded by ``X-Admin-Token`` header.

Used to trigger ETL jobs from outside (e.g. the deployed Railway service
itself, since the MySQL plugin's ``mysql.railway.internal`` is only
reachable from within Railway's network).

Set ``ADMIN_TOKEN`` in the environment to a long random string; the
caller must pass the same value in the ``X-Admin-Token`` request header.
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

admin_bp = Blueprint("admin", __name__)
logger = logging.getLogger(__name__)


def _check_token() -> bool:
    """Return True if the request's X-Admin-Token matches ADMIN_TOKEN."""
    expected = (current_app.config.get("ADMIN_TOKEN") or "").strip()
    if not expected:
        return False
    provided = (request.headers.get("X-Admin-Token") or "").strip()
    return provided != "" and provided == expected


def _run_subprocess(args, timeout):
    """Run a command from the project root; return (returncode, stdout, stderr, seconds)."""
    project_root = Path(current_app.root_path).resolve().parent
    t0 = time.time()
    proc = subprocess.run(
        args,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    return proc.returncode, proc.stdout, proc.stderr, time.time() - t0


@admin_bp.route("/admin/run-etl", methods=["POST"])
def run_etl():
    """Run the full ETL pipeline (daily + carbon) in the current process.

    Optional JSON body:
        {
            "skip_daily": false,    # skip the TradingView snapshot
            "skip_carbon": false,   # skip the Climatiq carbon backfill
            "min_revenue": 0        # min revenue for carbon backfill
        }

    Returns ``{daily: {...}, carbon: {...}}`` plus an overall ``ok`` flag.
    May take 1-3 minutes depending on the network and Climatiq quota.
    """
    if not _check_token():
        return jsonify({"error": "forbidden"}), 403

    body = request.get_json(silent=True) or {}
    skip_daily = bool(body.get("skip_daily", False))
    skip_carbon = bool(body.get("skip_carbon", False))
    min_revenue = float(body.get("min_revenue", 0) or 0)

    results = {"daily": None, "carbon": None, "ok": True}

    if not skip_daily:
        rc, out, err, secs = _run_subprocess(
            [sys.executable, "scripts/daily_etl.py"],
            timeout=300,
        )
        # Parse the last "DB totals" line as a quick summary
        summary = _last_matching(out, "DB totals:") or _last_matching(err, "DB totals:")
        results["daily"] = {
            "returncode": rc, "duration_s": round(secs, 1),
            "summary": summary, "stderr_tail": err[-500:] if err else "",
        }
        if rc != 0:
            results["ok"] = False

    if not skip_carbon and results["ok"]:
        rc, out, err, secs = _run_subprocess(
            [sys.executable, "scripts/carbon_etl.py", "--min-revenue", str(min_revenue)],
            timeout=600,
        )
        summary = _last_matching(out, "DB totals:") or _last_matching(err, "DB totals:")
        results["carbon"] = {
            "returncode": rc, "duration_s": round(secs, 1),
            "summary": summary, "stderr_tail": err[-500:] if err else "",
        }
        if rc != 0:
            results["ok"] = False

    return jsonify(results), (200 if results["ok"] else 500)


@admin_bp.route("/admin/status", methods=["GET"])
def admin_status():
    """Cheap health check (no token required) for cron monitoring."""
    return jsonify({
        "service": "low-carbon-backend",
        "etl": {
            "admin_token_set": bool((current_app.config.get("ADMIN_TOKEN") or "").strip()),
            "climatiq_key_set": bool((current_app.config.get("CLIMATIQ_API_KEY") or "").strip()),
        },
    })


def _last_matching(text: str, prefix: str) -> str | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        if prefix in line:
            return line.strip()
    return None
