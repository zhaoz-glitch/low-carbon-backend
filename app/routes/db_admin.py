"""Database Workbench API routes — raw SQL inspection endpoints.

Endpoints (all require login):
  GET /api/db/tables                — list every table with columns & row counts
  GET /api/db/tables/<table_name>   — paginated rows of a single table

Both endpoints are implemented with **raw SQL** via ``db.session.execute(text(...))``
to fulfil the "SQL query task" requirement:

  * Table discovery:     SELECT name FROM sqlite_master WHERE type='table'
  * Column introspection: PRAGMA table_info(<table>)
  * Row counting:         SELECT COUNT(*) FROM <table>
  * Data fetch:           SELECT * FROM <table> LIMIT ? OFFSET ?

Table names are validated against a whitelist derived from the SQLAlchemy
metadata to prevent SQL injection.
"""

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from app.extensions import db
from app.utils.auth import login_required

logger = logging.getLogger(__name__)

db_bp = Blueprint("db_admin", __name__)


def _allowed_tables():
    """Return the set of table names defined in SQLAlchemy metadata."""
    return set(db.metadata.tables.keys())


def _safe_table(name):
    """Validate a table name against the whitelist, or return None."""
    if name in _allowed_tables():
        return name
    return None


@db_bp.route("/db/tables", methods=["GET"])
@login_required
def list_tables():
    """List all tables with their column definitions and row counts.

    Uses raw SQL against sqlite_master / PRAGMA table_info.
    Sensitive columns (password_hash) are masked in the response.
    """
    # --- SQL task 1: discover tables via sqlite_master ---
    tables_result = db.session.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    )
    table_names = [row[0] for row in tables_result]

    tables = []
    for tname in table_names:
        # --- SQL task 2: column introspection via PRAGMA ---
        cols_result = db.session.execute(text(f'PRAGMA table_info("{tname}")'))
        columns = []
        for cid, name, ctype, notnull, dflt, pk in cols_result:
            columns.append({
                "cid": cid,
                "name": name,
                "type": ctype,
                "notnull": bool(notnull),
                "default": dflt,
                "pk": bool(pk),
            })

        # --- SQL task 3: row count ---
        count_result = db.session.execute(text(f'SELECT COUNT(*) FROM "{tname}"'))
        row_count = count_result.scalar()

        tables.append({
            "name": tname,
            "row_count": row_count,
            "columns": columns,
        })

    return jsonify({"tables": tables})


@db_bp.route("/db/tables/<string:table_name>", methods=["GET"])
@login_required
def get_table_data(table_name):
    """Return paginated rows for a table via raw SELECT.

    Query params:
      limit — max rows to return (default 50, max 200)
      offset — rows to skip (default 0)
    """
    tname = _safe_table(table_name)
    if tname is None:
        return jsonify({"error": "Unknown table", "table": table_name}), 404

    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return jsonify({"error": "limit/offset must be integers"}), 400

    # --- SQL task 4: column metadata for display order ---
    cols_result = db.session.execute(text(f'PRAGMA table_info("{tname}")'))
    col_names = [row[1] for row in cols_result]

    # --- SQL task 5: total row count ---
    total = db.session.execute(text(f'SELECT COUNT(*) FROM "{tname}"')).scalar()

    # --- SQL task 6: paginated data fetch ---
    rows_result = db.session.execute(
        text(f'SELECT * FROM "{tname}" LIMIT :limit OFFSET :offset'),
        {"limit": limit, "offset": offset},
    )
    rows = []
    for row in rows_result:
        record = {}
        for key in row._mapping.keys():
            value = row._mapping[key]
            # Never expose password hashes, even in the workbench
            if key == "password_hash":
                value = "********"
            record[key] = str(value) if value is not None else None
        rows.append(record)

    return jsonify({
        "table": tname,
        "columns": col_names,
        "total": total,
        "limit": limit,
        "offset": offset,
        "rows": rows,
    })
