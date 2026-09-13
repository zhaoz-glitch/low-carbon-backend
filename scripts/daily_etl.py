"""Daily ETL — refresh the whole US market, one snapshot row per company.

Designed to run once per day (ideally after the US market close).  Idempotent:
``financial_metrics`` is keyed by ``symbol`` alone, so re-running any number of
times per day updates the same rows.

Table responsibilities (see docs/schema.md):

    companies          identity only — symbol, name, sector, industry, exchange
    financial_metrics  exactly one row per symbol: the current market snapshot

All market writes go through ``app.services.market_snapshot_service`` so this
script cannot drift from the live-quote refresh path.

Usage:
    venv/Scripts/python scripts/daily_etl.py            # stocks only (default)
    venv/Scripts/python scripts/daily_etl.py --types all  # incl. DRs/funds
    venv/Scripts/python scripts/daily_etl.py --dry-run
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("daily_etl")

# company column limits (see app/models/company.py)
MAX_SYMBOL_LEN = 10
MAX_NAME_LEN = 200
MAX_SECTOR_LEN = 100
MAX_INDUSTRY_LEN = 200
MAX_EXCHANGE_LEN = 50


def _clean(value, max_len):
    if value is None:
        return None
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s[:max_len] if s else None


def fetch_universe(types: str = "stock"):
    """Whole US-market scan via the shared TradingView service."""
    from app.services.tradingview_service import tradingview_service

    return tradingview_service.fetch_financial_data(None, types=types) or []


def _sync_companies(db, Company, rows) -> tuple[int, int]:
    """Insert/refresh identity data.  No market figures are stored here."""
    existing = {c.symbol: c for c in db.session.query(Company).all()}

    new_rows = []
    update_rows = []
    for r in rows:
        symbol = r["symbol"][:MAX_SYMBOL_LEN]
        identity = {
            "symbol": symbol,
            "name": _clean(r.get("name"), MAX_NAME_LEN) or symbol,
            "sector": _clean(r.get("sector"), MAX_SECTOR_LEN),
            "industry": _clean(r.get("industry"), MAX_INDUSTRY_LEN),
            "exchange": _clean(r.get("exchange"), MAX_EXCHANGE_LEN),
        }
        current = existing.get(symbol)
        if current is None:
            new_rows.append(identity)
            continue

        changes = {}
        for field in ("name", "sector", "industry", "exchange"):
            if identity[field] and identity[field] != getattr(current, field):
                changes[field] = identity[field]
        if changes:
            update_rows.append({"symbol": symbol, **changes})

    if new_rows:
        db.session.execute(db.insert(Company), new_rows)
    if update_rows:
        db.session.bulk_update_mappings(Company, update_rows)
    db.session.commit()
    return len(new_rows), len(update_rows)


def run_etl(types: str = "stock", dry_run: bool = False) -> dict:
    from app import create_app
    from app.extensions import db
    from app.models.company import Company
    from app.services.market_snapshot_service import market_snapshot_service

    app = create_app()
    with app.app_context():
        rows = fetch_universe(types)
        rows = [r for r in rows if r.get("symbol")]
        logger.info("normalized %d valid rows", len(rows))

        if not rows:
            logger.error("TradingView returned no rows — nothing written")
            return {"fetched": 0, "written": 0, "companies_new": 0,
                    "companies_updated": 0}

        if dry_run:
            logger.info("dry-run: no database writes")
            return {"fetched": len(rows), "written": 0, "companies_new": 0,
                    "companies_updated": 0}

        # Companies first so the financial_metrics foreign key always resolves.
        new_c, upd_c = _sync_companies(db, Company, rows)
        logger.info("companies: %d new, %d updated", new_c, upd_c)

        written = market_snapshot_service.upsert(rows, source="daily_etl")
        logger.info("financial_metrics: %d snapshot rows written", written)

        total_companies = db.session.query(Company).count()
        coverage = market_snapshot_service.coverage()
        logger.info(
            "DB totals: companies=%d, financial_metrics=%d",
            total_companies, coverage["rows"],
        )
        return {
            "fetched": len(rows),
            "written": written,
            "companies_new": new_c,
            "companies_updated": upd_c,
            "total_companies": total_companies,
            "total_metrics": coverage["rows"],
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--types", default="stock", choices=["stock", "all"],
        help="symbol types to sync (default: stock only)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and normalize only, no DB writes",
    )
    args = parser.parse_args()

    t0 = time.time()
    try:
        stats = run_etl(types=args.types, dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001
        logger.error("ETL failed: %s", e, exc_info=True)
        sys.exit(1)

    logger.info("ETL done in %.1fs: %s", time.time() - t0, stats)


if __name__ == "__main__":
    main()
