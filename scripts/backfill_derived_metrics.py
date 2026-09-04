"""One-time backfill for derived detail-page metrics (no external API calls).

Fixes two NULL patterns in existing data:

1. ``carbon_emissions.carbon_change_yoy`` — rows written by ``carbon_etl.py``
   before the YoY fix hardcoded NULL.  Recompute as
   ``(I_y - I_{y-1}) / I_{y-1}`` wherever the previous report year exists.

2. ``financial_metrics.revenue_growth`` — never populated (TradingView's
   scanner exposes no revenue-growth column).  Derive from the carbon
   revenue history: ``(rev_y - rev_{y-1}) / rev_{y-1}`` using the latest two
   report years with revenue, written onto each symbol's latest row.

After this script, the daily ``carbon_etl.py`` run keeps both fields fresh.

Usage:
    venv/Scripts/python scripts/backfill_derived_metrics.py [--dry-run]
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_derived")


def main(dry_run: bool):
    from app import create_app
    from app.extensions import db
    from app.models.carbon_emission import CarbonEmission
    from app.models.financial_metric import FinancialMetric

    app = create_app()
    with app.app_context():
        # ---- 1) carbon_change_yoy for rows where it is NULL ----
        carbon_rows = CarbonEmission.query.all()
        by_key = {(r.symbol, r.report_year): r for r in carbon_rows}
        yoy_fixed = 0
        for row in carbon_rows:
            if row.carbon_change_yoy is not None or row.carbon_intensity_revenue is None:
                continue
            prev = by_key.get((row.symbol, row.report_year - 1))
            if not prev or not prev.carbon_intensity_revenue:
                continue
            prev_ci = float(prev.carbon_intensity_revenue)
            row.carbon_change_yoy = round(
                (float(row.carbon_intensity_revenue) - prev_ci) / prev_ci * 100, 2
            )
            yoy_fixed += 1
        logger.info("carbon_change_yoy: %d rows recomputed", yoy_fixed)

        # ---- 2) revenue_growth from carbon revenue history ----
        # Latest two report years with revenue, per symbol.
        rev_hist = {}
        for row in carbon_rows:
            if row.revenue is not None:
                rev_hist.setdefault(row.symbol, {})[row.report_year] = float(row.revenue)

        growth = {}
        for symbol, years in rev_hist.items():
            if len(years) < 2:
                continue
            y_old, y_new = sorted(years)[-2:]
            prev_rev = years[y_old]
            if prev_rev > 0:
                growth[symbol] = round((years[y_new] - prev_rev) / prev_rev * 100, 2)
        logger.info("revenue_growth computable for %d symbols", len(growth))

        # Latest financial row per symbol (max date).
        latest_ids = {}
        q = db.session.query(
            FinancialMetric.id, FinancialMetric.symbol, FinancialMetric.date
        ).order_by(FinancialMetric.date.desc())
        for fid, symbol, _date in q:
            if symbol not in latest_ids:
                latest_ids[symbol] = fid

        updates = [
            {"id": fid, "revenue_growth": growth[symbol]}
            for symbol, fid in latest_ids.items()
            if symbol in growth
        ]
        logger.info(
            "financial_metrics.revenue_growth: %d latest rows to update "
            "(dry_run=%s)", len(updates), dry_run,
        )

        if dry_run:
            db.session.rollback()
            for symbol in list(growth)[:5]:
                logger.info("  sample %s: revenue_growth=%s", symbol, growth[symbol])
            return

        if updates:
            db.session.bulk_update_mappings(FinancialMetric, updates)
        db.session.commit()
        logger.info("backfill committed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(args.dry_run)
