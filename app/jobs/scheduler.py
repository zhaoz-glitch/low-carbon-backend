"""APScheduler: US close market snapshot + monthly carbon refresh."""

from __future__ import annotations

import logging
import os

from flask import Flask

logger = logging.getLogger(__name__)

_scheduler = None


def start_scheduler(app: Flask):
    """Start background cron jobs once per process."""
    global _scheduler
    if _scheduler is not None:
        return
    if app.config.get("TESTING"):
        return
    if not app.config.get("SCHEDULER_ENABLED", True):
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false)")
        return
    # Flask debug reloader spawns two processes; only the child should run jobs.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from zoneinfo import ZoneInfo
    except ImportError:
        logger.warning("APScheduler not installed — daily close job will not auto-run")
        return

    tz = ZoneInfo(app.config.get("MARKET_TIMEZONE", "America/New_York"))
    hour, minute = _hhmm(app.config.get("MARKET_SYNC_CRON", "16:45"))

    scheduler = BackgroundScheduler(timezone=tz)
    app_obj = app

    def _market_job():
        with app_obj.app_context():
            from app.jobs.sync import sync_market

            result = sync_market(reason="scheduled-close")
            logger.info("Scheduled market sync: %s", result)

    def _carbon_job():
        with app_obj.app_context():
            from app.jobs.sync import sync_carbon

            result = sync_carbon(reason="scheduled-annual")
            logger.info("Scheduled carbon sync: %s", result)

    scheduler.add_job(
        _market_job,
        CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz),
        id="sync-market-close",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _carbon_job,
        CronTrigger(day=1, hour=6, minute=0, timezone="UTC"),
        id="sync-carbon-monthly",
        replace_existing=True,
        misfire_grace_time=86400,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: market %02d:%02d %s weekdays; carbon 1st of month 06:00 UTC",
        hour,
        minute,
        tz,
    )


def _hhmm(value: str) -> tuple[int, int]:
    try:
        parts = str(value).split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 16, 45
