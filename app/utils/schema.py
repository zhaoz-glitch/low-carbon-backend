"""Lightweight SQLite column patches.

``create_all()`` will not add columns to existing tables.  These ALTERs keep
a student/dev SQLite file in sync without wiping the database.
"""

import logging

from sqlalchemy import inspect, text

from app.extensions import db

logger = logging.getLogger(__name__)

_PATCHES = (
    ("companies", "isin", "ALTER TABLE companies ADD COLUMN isin VARCHAR(12)"),
    (
        "financial_metrics",
        "data_source",
        "ALTER TABLE financial_metrics ADD COLUMN data_source VARCHAR(50) DEFAULT 'mock'",
    ),
)


def ensure_schema():
    """Add missing columns used by the live-data pipeline."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in _PATCHES:
        if table not in tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        logger.info("Schema patch: adding %s.%s", table, column)
        db.session.execute(text(ddl))
        db.session.commit()
