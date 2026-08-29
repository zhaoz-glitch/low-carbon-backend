"""Daily ETL — pull the full US market from TradingView into the database.

Designed to run once per day (ideally after the US market close, e.g. 06:00
China time). Idempotent thanks to the ``uq_symbol_date`` unique constraint:
re-running on the same day updates the same rows instead of duplicating.

Pipeline:
    1. Fetch every US-market symbol from the TradingView scanner
       (price, volume, market cap, PE/PB, dividend yield, margins, sector…)
    2. Upsert ``companies``         (new symbols inserted, existing refreshed)
    3. Upsert ``financial_metrics`` (one row per symbol for today's date)
    4. Print a summary (the scheduled automation captures this as its report)

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
from datetime import date

import pandas as pd

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

# TradingView column -> FinancialMetric field
METRIC_MAP = {
    "close": "close",
    "volume": "volume",
    "market_cap_basic": "market_cap",
    "price_earnings_ttm": "pe_ttm",
    "price_book_value": "pb",
    "dividend_yield_recent": "dividend_yield",
    "turnover": "turnover",
    "change_1_year": "week_52_change",
    "net_margin": "net_profit_margin",
    "total_revenue": "revenue",
}

TV_COLUMNS = [
    "name", "description", "type", "exchange", "sector", "industry",
    "close", "volume", "market_cap_basic", "price_earnings_ttm",
    "price_book_value", "dividend_yield_recent", "turnover",
    "change_1_year", "net_margin", "total_revenue",
]


def fetch_universe(types: str = "stock") -> pd.DataFrame:
    from tradingview_screener import Query, col

    q = Query().select(*TV_COLUMNS).set_markets("america").where().limit(100_000)
    if types != "all":
        q = q.where(col("type") == types)
    total, df = q.get_scanner_data()
    logger.info("scanner returned %d symbols (type=%s)", total, types)
    return df


def _clean(value, max_len):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s[:max_len] if s else None


def _num(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return float(value)


def run_etl(types: str = "stock", dry_run: bool = False) -> dict:
    from app import create_app
    from app.extensions import db
    from app.models.company import Company
    from app.models.financial_metric import FinancialMetric

    app = create_app()
    with app.app_context():
        df = fetch_universe(types)

        # --- normalize rows ---
        rows = []
        for rec in df.to_dict("records"):
            symbol = _clean(rec.get("name"), MAX_SYMBOL_LEN)
            if not symbol:
                continue
            rows.append({
                "symbol": symbol,
                "name": _clean(rec.get("description"), MAX_NAME_LEN),
                "sector": _clean(rec.get("sector"), MAX_SECTOR_LEN),
                "industry": _clean(rec.get("industry"), MAX_INDUSTRY_LEN),
                "exchange": _clean(rec.get("exchange"), 50),
                "market_cap": _num(rec.get("market_cap_basic")),
                **{
                    field: _num(rec.get(col))
                    for col, field in METRIC_MAP.items()
                },
            })
        logger.info("normalized %d valid rows", len(rows))

        if dry_run:
            logger.info("dry-run: no database writes")
            return {"fetched": len(rows), "inserted": 0, "updated": 0}

        today = date.today()

        # --- upsert companies ---
        existing_companies = {
            c.symbol: c
            for c in db.session.query(Company).all()
        }
        new_company_rows = []
        updated_company_rows = []
        for r in rows:
            current = existing_companies.get(r["symbol"])
            if current is None:
                new_company_rows.append({
                    k: r[k] for k in
                    ("symbol", "name", "sector", "industry", "exchange",
                     "market_cap")
                })
            else:
                updates = {
                    "symbol": current.symbol,
                    "name": r["name"] or current.name,
                    "sector": r["sector"] or current.sector,
                    "industry": r["industry"] or current.industry,
                    "exchange": r["exchange"] or current.exchange,
                    "market_cap": r["market_cap"] or (
                        float(current.market_cap)
                        if current.market_cap is not None else None
                    ),
                }
                if (updates["name"] != current.name
                        or updates["sector"] != current.sector
                        or updates["industry"] != current.industry
                        or updates["exchange"] != current.exchange
                        or updates["market_cap"] != (
                            float(current.market_cap)
                            if current.market_cap is not None else None)):
                    updated_company_rows.append(updates)

        if new_company_rows:
            db.session.execute(db.insert(Company), new_company_rows)
        if updated_company_rows:
            db.session.bulk_update_mappings(Company, updated_company_rows)
        logger.info(
            "companies: %d new, %d updated (existing total %d)",
            len(new_company_rows), len(updated_company_rows),
            len(existing_companies),
        )

        # --- upsert financial_metrics for today ---
        existing_today = {
            fm.symbol: fm.id
            for fm in db.session.query(
                FinancialMetric.id, FinancialMetric.symbol
            ).filter(FinancialMetric.date == today).all()
        }

        new_metric_rows = []
        update_metric_rows = []
        for r in rows:
            metric = {k: r[k] for k in METRIC_MAP.values()}
            fm_id = existing_today.get(r["symbol"])
            if fm_id is None:
                new_metric_rows.append({
                    "symbol": r["symbol"], "date": today, **metric,
                })
            else:
                update_metric_rows.append({
                    "id": fm_id, **metric,
                })

        if new_metric_rows:
            db.session.execute(db.insert(FinancialMetric), new_metric_rows)
        if update_metric_rows:
            db.session.bulk_update_mappings(
                FinancialMetric, update_metric_rows
            )
        logger.info(
            "financial_metrics (%s): %d new, %d updated",
            today, len(new_metric_rows), len(update_metric_rows),
        )

        db.session.commit()

        total_companies = db.session.query(Company).count()
        total_metrics = db.session.query(FinancialMetric).count()
        logger.info(
            "DB totals: companies=%d, financial_metrics=%d",
            total_companies, total_metrics,
        )
        return {
            "fetched": len(rows),
            "inserted": len(new_metric_rows),
            "updated": len(update_metric_rows),
            "total_companies": total_companies,
            "total_metrics": total_metrics,
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
