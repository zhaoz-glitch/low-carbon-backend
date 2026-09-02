"""Lightweight schema sync — add missing columns at boot.

``db.create_all()`` only creates missing *tables*; it never alters existing
ones.  Several times now a new model column (e.g. ``financial_metrics.revenue``,
``companies.isin``) has been deployed while the production MySQL table still
had the old shape, turning every ORM query on that table into a 500.

``ensure_schema()`` compares the ORM metadata against the live database and
issues ``ALTER TABLE ... ADD COLUMN`` for anything missing.  It is idempotent
and safe to run on every boot (SQLite and MySQL dialects supported).
"""

import logging

from sqlalchemy import inspect, text

logger = logging.getLogger(__name__)


def ensure_schema(db):
    """Add any columns present in the ORM metadata but missing in the DB."""
    try:
        insp = inspect(db.engine)
        existing_tables = set(insp.get_table_names())
    except Exception as e:
        logger.warning("schema sync: inspect failed (%s) — skipped", e)
        return

    added = []
    for table_name, table in db.metadata.tables.items():
        if table_name not in existing_tables:
            continue  # create_all already handles brand-new tables
        try:
            existing_cols = {c["name"] for c in insp.get_columns(table_name)}
        except Exception:
            continue

        for column in table.columns:
            if column.name in existing_cols:
                continue
            try:
                col_type = column.type.compile(db.engine.dialect)
                if column.nullable:
                    col_type += " NULL"
                else:
                    # Adding a NOT NULL column to a non-empty table needs a
                    # server default; fall back to nullable to stay safe.
                    col_type += " NULL"
                db.session.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}")
                )
                db.session.commit()
                added.append(f"{table_name}.{column.name}")
            except Exception as e:
                db.session.rollback()
                logger.warning(
                    "schema sync: could not add %s.%s (%s)",
                    table_name, column.name, e,
                )

    if added:
        logger.info("schema sync: added columns %s", added)
