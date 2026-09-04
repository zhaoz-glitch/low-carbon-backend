"""Carbon ETL — spend-based emissions backfill for the whole US market.

Uses Climatiq (https://www.climatiq.io) spend-based emission factors:
for each TradingView sector we make ONE ``/data/v1/search`` call plus
ONE ``/data/v1/estimate`` probe ($1M) to learn the effective factor
(kg CO2e per USD of sector output).  Every company's emissions are then
computed locally as ``factor × revenue`` — a full-market backfill costs
only ~2 × (number of sectors) API calls (~20 in total).

The result is an *estimate*, not a disclosure: scope1/scope2 are left
NULL (spend-based factors are combined figures) and ``data_source`` is
set to ``climatiq`` so the UI can label it accordingly.

Requires ``CLIMATIQ_API_KEY`` in the environment (.env).

Usage:
    venv/Scripts/python scripts/carbon_etl.py               # full backfill
    venv/Scripts/python scripts/carbon_etl.py --dry-run     # no DB writes
    venv/Scripts/python scripts/carbon_etl.py --min-revenue 100000000
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("carbon_etl")

MAX_SYMBOL_LEN = 10
MAX_SECTOR_LEN = 100

TV_COLUMNS = ["name", "type", "sector", "total_revenue", "market_cap_basic"]


def fetch_universe() -> pd.DataFrame:
    from tradingview_screener import Query, col

    q = (
        Query()
        .select(*TV_COLUMNS)
        .set_markets("america")
        .where(col("type") == "stock")
        .limit(100_000)
    )
    total, df = q.get_scanner_data()
    logger.info("scanner returned %d US stocks", total)
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


def run_etl(min_revenue: float = 0.0, dry_run: bool = False) -> dict:
    from app import create_app
    from app.extensions import db
    from app.models.company import Company
    from app.models.financial_metric import FinancialMetric
    from app.models.carbon_emission import CarbonEmission
    from app.services.carbon_service import carbon_service

    app = create_app()
    with app.app_context():
        if not carbon_service._climatiq_key and not dry_run:
            logger.error(
                "CLIMATIQ_API_KEY is not set — add it to .env and re-run"
            )
            sys.exit(2)

        df = fetch_universe()

        # --- normalize ---
        rows = []
        for rec in df.to_dict("records"):
            symbol = _clean(rec.get("name"), MAX_SYMBOL_LEN)
            if not symbol:
                continue
            revenue = _num(rec.get("total_revenue"))
            if revenue is None or revenue < min_revenue:
                continue
            rows.append({
                "symbol": symbol,
                "sector": _clean(rec.get("sector"), MAX_SECTOR_LEN) or "Other",
                "revenue": revenue,
            })
        logger.info("normalized %d companies with revenue data", len(rows))

        # --- resolve one spend-based factor per sector (~2 calls each) ---
        sectors = sorted({r["sector"] for r in rows})
        factors = {}
        for sector in sectors:
            entry = carbon_service.get_sector_factor(sector)
            if entry:
                factors[sector] = entry
                logger.info(
                    "  %s: %s = %.6g kg CO2e/USD",
                    sector, entry["factor_name"], entry["factor_kg_per_usd"],
                )
            else:
                logger.warning("  %s: no factor resolved — sector skipped", sector)
        if not factors:
            logger.error("No Climatiq factors resolved — aborting")
            sys.exit(3)

        # --- compute emissions locally ---
        year = datetime.now().year

        # Previous report year history — the spend-based estimate cannot
        # provide YoY metrics directly, so derive them from what we already
        # store: carbon_change_yoy = (I_y - I_{y-1}) / I_{y-1} and
        # revenue_growth = (rev_y - rev_{y-1}) / rev_{y-1}.
        prev_year = {
            ce.symbol: (ce.carbon_intensity_revenue, ce.revenue)
            for ce in db.session.query(
                CarbonEmission.symbol,
                CarbonEmission.carbon_intensity_revenue,
                CarbonEmission.revenue,
            ).filter(CarbonEmission.report_year == year - 1).all()
        }

        carbon_rows = []
        for r in rows:
            entry = factors.get(r["sector"])
            if not entry:
                continue
            total_t = r["revenue"] * entry["factor_kg_per_usd"] / 1000.0
            intensity = round(entry["factor_kg_per_usd"] * 1000.0, 4)
            prev_ci, prev_rev = prev_year.get(r["symbol"], (None, None))
            prev_ci = float(prev_ci) if prev_ci is not None else None
            yoy = (
                round((intensity - prev_ci) / prev_ci * 100, 2)
                if prev_ci
                else None
            )
            carbon_rows.append({
                "symbol": r["symbol"],
                "report_year": year,
                "scope1": None,
                "scope2": None,
                "total_emissions": round(total_t, 2),
                "carbon_intensity_revenue": intensity,
                "carbon_change_yoy": yoy,
                "revenue": round(r["revenue"], 2),
                "data_source": "climatiq",
                "has_carbon_data": True,
            })
        logger.info("computed %d carbon estimates", len(carbon_rows))

        # Revenue growth vs the previous report year, per symbol.
        rev_growth = {
            r["symbol"]: round(
                (r["revenue"] - float(prev_year[r["symbol"]][1]))
                / float(prev_year[r["symbol"]][1]) * 100, 2
            )
            for r in rows
            if r["symbol"] in prev_year and prev_year[r["symbol"]][1]
        }
        logger.info(
            "revenue_growth computable for %d symbols (prev year %d)",
            len(rev_growth), year - 1,
        )

        if dry_run:
            top = sorted(
                carbon_rows, key=lambda x: -x["total_emissions"]
            )[:5]
            for t in top:
                logger.info(
                    "  top: %s = %.0f tCO2e (rev %.0fM USD)",
                    t["symbol"], t["total_emissions"], t["revenue"] / 1e6,
                )
            return {"computed": len(carbon_rows), "sectors": len(factors)}

        # --- upsert companies (ensure FK targets exist) ---
        known = {
            c.symbol
            for c in db.session.query(Company.symbol).all()
        }
        missing = [
            {"symbol": r["symbol"], "sector": r["sector"]}
            for r in rows if r["symbol"] not in known
        ]
        if missing:
            db.session.execute(db.insert(Company), missing)
            logger.info("companies: %d inserted (FK targets)", len(missing))

        # --- persist revenue into financial_metrics for today ---
        today = date.today()
        existing_today = {
            fm.symbol: fm.id
            for fm in db.session.query(
                FinancialMetric.id, FinancialMetric.symbol
            ).filter(FinancialMetric.date == today).all()
        }
        rev_updates = [
            {
                "id": existing_today[r["symbol"]],
                "revenue": r["revenue"],
                **({"revenue_growth": rev_growth[r["symbol"]]}
                   if r["symbol"] in rev_growth else {}),
            }
            for r in rows
            if r["symbol"] in existing_today
        ]
        rev_inserts = [
            {
                "symbol": r["symbol"], "date": today,
                "revenue": r["revenue"],
                "revenue_growth": rev_growth.get(r["symbol"]),
            }
            for r in rows if r["symbol"] not in existing_today
        ]
        if rev_updates:
            db.session.bulk_update_mappings(FinancialMetric, rev_updates)
        if rev_inserts:
            db.session.execute(db.insert(FinancialMetric), rev_inserts)
        logger.info(
            "financial_metrics.revenue: %d updated, %d inserted",
            len(rev_updates), len(rev_inserts),
        )

        # --- upsert carbon_emissions ---
        existing_carbon = {
            (ce.symbol, ce.report_year): ce.id
            for ce in db.session.query(
                CarbonEmission.id, CarbonEmission.symbol,
                CarbonEmission.report_year
            ).filter(CarbonEmission.report_year == year).all()
        }
        new_carbon = [
            r for r in carbon_rows
            if (r["symbol"], year) not in existing_carbon
        ]
        upd_carbon = [
            {**r, "id": existing_carbon[(r["symbol"], year)]}
            for r in carbon_rows
            if (r["symbol"], year) in existing_carbon
        ]
        if new_carbon:
            db.session.execute(db.insert(CarbonEmission), new_carbon)
        if upd_carbon:
            db.session.bulk_update_mappings(CarbonEmission, upd_carbon)
        db.session.commit()
        logger.info(
            "carbon_emissions (%d): %d new, %d updated",
            year, len(new_carbon), len(upd_carbon),
        )

        total_carbon = db.session.query(CarbonEmission).count()
        with_data = (
            db.session.query(CarbonEmission)
            .filter(CarbonEmission.has_carbon_data.is_(True))
            .count()
        )
        logger.info(
            "DB totals: carbon_emissions=%d, with_carbon_data=%d",
            total_carbon, with_data,
        )
        return {
            "computed": len(carbon_rows),
            "sectors": len(factors),
            "inserted": len(new_carbon),
            "updated": len(upd_carbon),
            "total_carbon": total_carbon,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-revenue", type=float, default=0.0,
        help="skip companies below this annual revenue (USD)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and compute only, no DB writes",
    )
    args = parser.parse_args()

    t0 = time.time()
    try:
        stats = run_etl(min_revenue=args.min_revenue, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("carbon ETL failed: %s", e, exc_info=True)
        sys.exit(1)

    logger.info("carbon ETL done in %.1fs: %s", time.time() - t0, stats)


if __name__ == "__main__":
    main()
