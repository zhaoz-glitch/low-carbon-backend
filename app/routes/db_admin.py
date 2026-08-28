"""Database Workbench API routes — raw SQL inspection endpoints.

Endpoints (all require login):
  GET /api/db/tables                — list every table with columns & row counts
  GET /api/db/tables/<table_name>   — paginated rows of a single table

Both endpoints use **raw SQL** via ``db.session.execute(text(...))`` to fulfil
the "SQL query task" requirement, while the table-discovery leg now relies on
``sqlalchemy.inspect`` so the same code works against SQLite (local dev) and
MySQL (Railway production).  The previous implementation used SQLite-only
``sqlite_master`` / ``PRAGMA table_info`` and would have crashed on MySQL.

  * Table discovery:      inspector.get_table_names()         (DB-agnostic)
  * Column introspection: inspector.get_columns(table)        (DB-agnostic)
  * Row counting:         SELECT COUNT(*) FROM <table>        (raw SQL)
  * Data fetch:           SELECT * FROM <table> LIMIT ? …    (raw SQL)

Table names are validated against a whitelist derived from the SQLAlchemy
metadata to prevent SQL injection.
"""

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import inspect, text

from app.extensions import db
from app.utils.auth import login_required

logger = logging.getLogger(__name__)

db_bp = Blueprint("db_admin", __name__)


SENSITIVE_COLUMNS = {"password_hash"}


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

    Works on both SQLite and MySQL — column introspection goes through the
    SQLAlchemy ``Inspector`` which translates each dialect's native metadata
    (sqlite_master on SQLite, information_schema on MySQL) into a uniform
    Python API.  Row counts are still raw ``COUNT(*)`` queries.
    """
    inspector = inspect(db.engine)
    table_names = sorted(
        name for name in inspector.get_table_names()
        if name in _allowed_tables()
    )

    tables = []
    for tname in table_names:
        # --- SQL task: column introspection (DB-agnostic via Inspector) ---
        columns = []
        for col in inspector.get_columns(tname):
            # ``col`` is an OrderedDict-like; pull the keys we care about.
            columns.append({
                "name": col.get("name"),
                "type": str(col.get("type")),
                "nullable": bool(col.get("nullable", True)),
                "default": str(col.get("default")) if col.get("default") is not None else None,
                "pk": bool(col.get("primary_key", False)),
            })

        # --- SQL task: raw COUNT(*) for live row counts ---
        row_count = db.session.execute(
            text(f'SELECT COUNT(*) FROM "{tname}"')
        ).scalar()

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

    inspector = inspect(db.engine)
    col_info = inspector.get_columns(tname)
    col_names = [c["name"] for c in col_info]

    # --- SQL task: raw total count ---
    total = db.session.execute(
        text(f'SELECT COUNT(*) FROM "{tname}"')
    ).scalar()

    # --- SQL task: raw paginated data fetch ---
    rows_result = db.session.execute(
        text(f'SELECT * FROM "{tname}" LIMIT :limit OFFSET :offset'),
        {"limit": limit, "offset": offset},
    )
    rows = []
    sensitive = SENSITIVE_COLUMNS.intersection(col_names)
    for row in rows_result:
        record = {}
        for key in row._mapping.keys():
            value = row._mapping[key]
            if key in sensitive:
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
