"""CLI: python -m app.jobs.cli sync-market | sync-carbon | sync-all"""

from __future__ import annotations

import argparse
import json
import os

# One-shot CLI should not start the weekday scheduler.
os.environ.setdefault("SCHEDULER_ENABLED", "false")

from app import create_app
from app.jobs.sync import sync_carbon, sync_market


def main():
    parser = argparse.ArgumentParser(description="Low-carbon screener data sync")
    parser.add_argument(
        "job",
        choices=["sync-market", "sync-carbon", "sync-all"],
        help="Which pipeline to run",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        if args.job in ("sync-market", "sync-all"):
            print(json.dumps(sync_market(reason="cli"), ensure_ascii=False, indent=2, default=str))
        if args.job in ("sync-carbon", "sync-all"):
            print(json.dumps(sync_carbon(reason="cli"), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
