"""Carbon history backfill — extrapolate 2020-2025 rows from latest year.

The latest-year carbon rows come from Climatiq / Clarity AI / Bavest and
only cover a single reporting year.  The 5-year trend chart in the stock
drawer needs prior years too, but the upstream providers don't return
multi-year data without paid subscriptions.

This script fills the gap synthetically:

  historical_intensity = current_intensity / (1 + ci_yoy) ** year_diff
  historical_revenue   = current_revenue   / (1 + rev_yoy) ** year_diff
  historical_total     = intensity * revenue / 1e6

``ci_yoy`` and ``rev_yoy`` come from ``SECTOR_TRENDS`` in
``app.services.carbon_service`` — sector-typical averages from EPA
industry data and long-run US GDP/sector growth.

New rows are stamped ``data_source="backfill"`` so the UI / API can
distinguish estimates from disclosures.

Usage:
    venv/Scripts/python scripts/backfill_carbon_history.py
    venv/Scripts/python scripts/backfill_carbon_history.py --dry-run
    venv/Scripts/python scripts/backfill_carbon_history.py --year-start 2021 --year-end 2024
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("carbon_backfill")


def run(year_start: int, year_end: int, dry_run: bool) -> dict:
    from app import create_app
    from app.services.carbon_service import carbon_service

    app = create_app()
    with app.app_context():
        result = carbon_service.backfill_history(
            year_start=year_start, year_end=year_end, dry_run=dry_run,
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--year-start", type=int, default=2020,
                        help="First historical year to fill (inclusive). Default: 2020")
    parser.add_argument("--year-end", type=int, default=2025,
                        help="Last historical year to fill (inclusive, before latest). Default: 2025")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute everything but roll back instead of committing.")
    args = parser.parse_args()

    if args.year_start > args.year_end:
        logger.error("--year-start must be <= --year-end")
        sys.exit(2)

    t0 = os.environ.get("BACKFILL_START")
    if t0:
        import time
        logger.info("Starting backfill (years %d..%d, dry_run=%s)",
                    args.year_start, args.year_end, args.dry_run)

    result = run(args.year_start, args.year_end, args.dry_run)
    logger.info("Backfill summary: %s", result)
    # Print a single line that admin.py can grep for the status block
    print(f"BACKFILL_RESULT inserted={result['inserted']} "
          f"skipped={result['skipped']} errors={result['errors']} "
          f"dry_run={result['dry_run']}")
    sys.exit(0 if result["errors"] == 0 else 1)


if __name__ == "__main__":
    main()