"""One-time refactor: collapse ``financial_metrics`` to one row per company.

Before
------
``financial_metrics`` was a dated snapshot log keyed by ``(symbol, date)``.
Every ETL run appended a new row, so a symbol accumulated one row per run day
(TSLA had 4).  All read paths only ever looked at ``MAX(date)``, which meant
the newest — and often *partial* — live-quote row silently shadowed the
complete daily-ETL row.  Meanwhile ``companies.market_cap`` duplicated
``financial_metrics.market_cap`` and drifted.

After
-----
``financial_metrics``   one row per ``symbol`` (``uq_financial_metrics_symbol``)
                        columns: as_of_date, updated_at, market + fundamentals
``companies``           static identity only — market_cap column dropped

What this script does
---------------------
1. Back up ``companies.market_cap`` into ``companies_market_cap_backup``.
2. Merge each symbol's dated rows into a single snapshot, per column taking
   the **newest non-NULL value** (so a partial live row can never erase a
   field the full daily row had populated).  Missing ``market_cap`` falls back
   to the backed-up ``companies.market_cap``.
3. Rebuild ``financial_metrics`` with the new schema and swap it in.
4. ``DROP COLUMN companies.market_cap``.

Idempotent: re-running after a successful migration is a no-op (detected by
the absence of the legacy ``date`` column) and simply reports current state.

Usage
-----
    venv/Scripts/python scripts/refactor_financial_snapshot.py --dry-run
    venv/Scripts/python scripts/refactor_financial_snapshot.py --apply
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("refactor_snapshot")

BACKUP_TABLE = "companies_market_cap_backup"
NEW_TABLE = "financial_metrics__refactor"

# Columns merged across a symbol's dated rows ("newest non-NULL wins").
MERGE_COLUMNS = (
    "close",
    "volume",
    "market_cap",
    "pe_ttm",
    "pb",
    "dividend_yield",
    "turnover",
    "week_52_change",
    "net_profit_margin",
    "revenue_growth",
    "revenue",
)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

def _columns(insp, table: str) -> set[str]:
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:  # noqa: BLE001 — table missing
        return set()


def _tables(insp) -> set[str]:
    try:
        return set(insp.get_table_names())
    except Exception:  # noqa: BLE001
        return set()


def merge_rows(rows: list[dict]) -> tuple[dict | None, dict]:
    """Collapse one symbol's dated rows into a single snapshot dict.

    Returns ``(merged_fields, meta)`` where ``meta`` reports how many rows were
    folded in and whether the merge actually recovered any NULLs.
    """
    dated = [r for r in rows if r.get("date") is not None]
    dated.sort(key=lambda r: r["date"])
    if not dated:
        dated = sorted(rows, key=lambda r: str(r.get("date") or ""))

    merged: dict = {}
    filled_from_older = 0
    newest_close_index = -1

    for index, row in enumerate(dated):
        for col in MERGE_COLUMNS:
            value = row.get(col)
            if value is None:
                continue
            if col not in merged:
                if index < len(dated) - 1:
                    filled_from_older += 1
            merged[col] = value
        if row.get("close") is not None:
            newest_close_index = index

    # as_of_date = the trading day of the newest row that actually had a price
    if newest_close_index >= 0:
        as_of = dated[newest_close_index]["date"]
    else:
        as_of = dated[-1]["date"]

    source = None
    if newest_close_index >= 0:
        source = dated[newest_close_index].get("data_source")

    meta = {
        "folded_rows": len(dated),
        "recovered_from_older": filled_from_older,
        "as_of_date": as_of,
        "data_source": source,
    }
    return merged, meta


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------

def run(apply: bool = False) -> dict:
    from sqlalchemy import inspect, text

    from app import create_app
    from app.extensions import db
    from app.models.financial_metric import FinancialMetric

    app = create_app()
    report: dict = {"applied": bool(apply)}

    with app.app_context():
        insp = inspect(db.engine)
        tables = _tables(insp)
        fm_cols = _columns(insp, "financial_metrics")
        co_cols = _columns(insp, "companies")

        report["before"] = {
            "financial_metrics_rows": db.session.execute(
                text("SELECT COUNT(*) FROM financial_metrics")
            ).scalar(),
            "financial_metrics_has_date": "date" in fm_cols,
            "financial_metrics_has_as_of_date": "as_of_date" in fm_cols,
            "companies_has_market_cap": "market_cap" in co_cols,
        }

        if "financial_metrics" not in tables:
            report["error"] = "financial_metrics table not found"
            return report

        already_done = "as_of_date" in fm_cols and "date" not in fm_cols
        if already_done:
            logger.info("financial_metrics already refactored — skipping collapse")

        # ----------------------------------------------------------
        # 1) read + merge
        # ----------------------------------------------------------
        merged_payload: list[dict] = []
        if not already_done:
            raw = db.session.execute(
                text("SELECT * FROM financial_metrics")
            ).mappings().all()
            logger.info("read %d legacy rows", len(raw))

            grouped: dict[str, list[dict]] = defaultdict(list)
            for row in raw:
                grouped[row["symbol"]].append(dict(row))

            recovered = 0
            for symbol, rows in grouped.items():
                merged, meta = merge_rows(rows)
                if merged is None:
                    continue
                recovered += meta["recovered_from_older"]
                merged_payload.append(
                    {
                        "symbol": symbol,
                        "as_of_date": meta["as_of_date"],
                        "data_source": meta["data_source"] or "refactor-merge",
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                        **merged,
                    }
                )

            report["merge"] = {
                "symbols": len(merged_payload),
                "legacy_rows": len(raw),
                "rows_removed": len(raw) - len(merged_payload),
                "nulls_recovered_from_older_rows": recovered,
            }
            logger.info("merged into %d snapshot rows (%d recovered fields)",
                        len(merged_payload), recovered)

            # ---- market_cap fallback from the companies column ----
            if "market_cap" in co_cols:
                fallback = {
                    s: mc
                    for s, mc in db.session.execute(
                        text("SELECT symbol, market_cap FROM companies")
                    ).all()
                    if mc is not None
                }
                patched = 0
                for row in merged_payload:
                    if row.get("market_cap") is None and row["symbol"] in fallback:
                        row["market_cap"] = fallback[row["symbol"]]
                        patched += 1
                report["market_cap_fallback"] = patched
                logger.info("market_cap filled from companies for %d symbols", patched)

        # ----------------------------------------------------------
        # 2) dry-run stops here
        # ----------------------------------------------------------
        if not apply:
            report["dry_run"] = True
            if not already_done:
                sample = [r for r in merged_payload if r.get("close") is not None][:1]
                report["sample"] = [
                    {k: (str(v) if isinstance(v, (date, datetime)) else v)
                     for k, v in s.items()}
                    for s in sample
                ]
            return report

        # ----------------------------------------------------------
        # 3) rebuild financial_metrics
        # ----------------------------------------------------------
        if not already_done:
            if NEW_TABLE in tables:
                db.session.execute(text(f"DROP TABLE {NEW_TABLE}"))
                db.session.commit()

            # clone companies first so the FK on the new table can resolve
            from app.models.company import Company

            target_meta = db.MetaData()
            Company.__table__.to_metadata(target_meta, name="companies")
            new_table = FinancialMetric.__table__.to_metadata(
                target_meta, name=NEW_TABLE
            )
            new_table.create(bind=db.engine)

            col_names = [c.name for c in new_table.columns]

            def _as_date(v):
                if v is None or isinstance(v, date):
                    return v
                if isinstance(v, datetime):
                    return v.date()
                return date.fromisoformat(str(v)[:10])

            payload = [
                {k: (_as_date(row.get(k)) if k == "as_of_date" else row.get(k))
                 for k in col_names}
                for row in merged_payload
            ]
            if payload:
                db.session.execute(new_table.insert(), payload)
                db.session.commit()
            logger.info("wrote %d rows into %s", len(payload), NEW_TABLE)

            dialect = db.engine.dialect.name
            if dialect == "sqlite":
                db.session.execute(text("PRAGMA foreign_keys=OFF"))
                db.session.execute(text(f"DROP TABLE financial_metrics"))
                db.session.execute(
                    text(f"ALTER TABLE {NEW_TABLE} RENAME TO financial_metrics")
                )
                db.session.execute(text("PRAGMA foreign_keys=ON"))
                db.session.commit()
            else:
                db.session.execute(text(f"DROP TABLE financial_metrics"))
                db.session.execute(
                    text(f"RENAME TABLE {NEW_TABLE} TO financial_metrics")
                )
                db.session.commit()
            logger.info("swapped %s in as financial_metrics", NEW_TABLE)

        # ----------------------------------------------------------
        # 4) drop the duplicated companies.market_cap
        # ----------------------------------------------------------
        if "market_cap" in co_cols:
            if BACKUP_TABLE not in tables:
                db.session.execute(
                    text(
                        f"CREATE TABLE {BACKUP_TABLE} AS "
                        f"SELECT symbol, market_cap, "
                        f"CURRENT_TIMESTAMP AS backed_up_at FROM companies"
                    )
                )
                db.session.commit()
                logger.info("backed up companies.market_cap into %s", BACKUP_TABLE)
            else:
                logger.info("%s already exists — keeping existing backup", BACKUP_TABLE)

            db.session.execute(text("ALTER TABLE companies DROP COLUMN market_cap"))
            db.session.commit()
            logger.info("dropped companies.market_cap")

        # ----------------------------------------------------------
        # 5) verify
        # ----------------------------------------------------------
        total = db.session.execute(
            text("SELECT COUNT(*) FROM financial_metrics")
        ).scalar() or 0
        distinct = db.session.execute(
            text("SELECT COUNT(DISTINCT symbol) FROM financial_metrics")
        ).scalar() or 0
        report["after"] = {
            "financial_metrics_rows": total,
            "distinct_symbols": distinct,
            "duplicates": total - distinct,
        }
        report["applied"] = True
        logger.info("REFACTOR_RESULT %s",
                    json.dumps(report.get("merge", {}), default=str))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="perform the migration (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit no-op flag (default behaviour)")
    args = parser.parse_args()

    report = run(apply=args.apply and not args.dry_run)
    print("REFACTOR_REPORT " + json.dumps(report, default=str))
    if args.apply and report.get("after", {}).get("duplicates"):
        sys.exit(2)


if __name__ == "__main__":
    main()
