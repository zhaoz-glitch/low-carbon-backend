"""Upsert live market / carbon payloads into SQLite (or Postgres)."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.carbon_emission import CarbonEmission
from app.models.company import Company
from app.models.data_sync_log import DataSyncLog
from app.models.financial_metric import FinancialMetric
from app.services.carbon_service import carbon_service
from app.services.tradingview_service import tradingview_service
from app.universe import COMPANY_ISINS, universe_symbols

logger = logging.getLogger(__name__)


def _finish_log(log: DataSyncLog, status: str, source: str, rows: int, message: str):
    log.status = status
    log.source = source
    log.rows_upserted = rows
    log.message = message
    log.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return log.to_dict()


def seed_company_isins():
    """Fill companies.isin for the known universe (idempotent)."""
    updated = 0
    for symbol, isin in COMPANY_ISINS.items():
        company = Company.query.filter_by(symbol=symbol).first()
        if company and company.isin != isin:
            company.isin = isin
            updated += 1
    if updated:
        db.session.commit()
        logger.info("Updated ISINs for %s companies", updated)


def recover_stale_jobs(minutes: int = 0):
    """Mark leftover 'running' jobs as failed after a process restart."""
    stale = DataSyncLog.query.filter_by(status="running").all()
    now = datetime.now(timezone.utc)
    for row in stale:
        row.status = "failed"
        row.message = ((row.message or "") + " (interrupted)").strip()
        row.finished_at = now
    if stale:
        db.session.commit()
        logger.info("Marked %s stale sync jobs as failed", len(stale))


def upsert_financial_rows(rows: list[dict]) -> int:
    """Insert or update financial_metrics by (symbol, date)."""
    count = 0
    for row in rows:
        symbol = row.get("symbol")
        as_of = row.get("date")
        if not symbol or not as_of:
            continue
        if Company.query.filter_by(symbol=symbol).first() is None:
            continue
        existing = FinancialMetric.query.filter_by(symbol=symbol, date=as_of).first()
        fields = {
            "close": row.get("close"),
            "volume": row.get("volume"),
            "market_cap": row.get("market_cap"),
            "pe_ttm": row.get("pe_ttm"),
            "pb": row.get("pb"),
            "dividend_yield": row.get("dividend_yield"),
            "turnover": row.get("turnover"),
            "week_52_change": row.get("week_52_change"),
            "net_profit_margin": row.get("net_profit_margin"),
            "revenue_growth": row.get("revenue_growth"),
            "data_source": row.get("data_source") or "tradingview",
        }
        if existing:
            for key, val in fields.items():
                if val is not None:
                    setattr(existing, key, val)
        else:
            db.session.add(FinancialMetric(symbol=symbol, date=as_of, **fields))
        company = Company.query.filter_by(symbol=symbol).first()
        if company and fields.get("market_cap") is not None:
            company.market_cap = fields["market_cap"]
        count += 1
    db.session.commit()
    return count


def _yoy_from_previous(symbol: str, report_year: int, intensity) -> float | None:
    if intensity is None:
        return None
    prev = (
        CarbonEmission.query.filter_by(symbol=symbol, report_year=report_year - 1).first()
    )
    if not prev or prev.carbon_intensity_revenue is None:
        return None
    prev_ci = float(prev.carbon_intensity_revenue)
    if not prev_ci:
        return None
    return round((float(intensity) - prev_ci) / prev_ci * 100, 2)


def upsert_carbon_rows(rows: list[dict]) -> int:
    count = 0
    for row in rows:
        symbol = row.get("symbol")
        year = row.get("report_year")
        if not symbol or not year:
            continue
        if Company.query.filter_by(symbol=symbol).first() is None:
            continue
        if row.get("carbon_change_yoy") is None:
            row["carbon_change_yoy"] = _yoy_from_previous(
                symbol, year, row.get("carbon_intensity_revenue")
            )
        existing = CarbonEmission.query.filter_by(symbol=symbol, report_year=year).first()
        payload = {
            "scope1": row.get("scope1"),
            "scope2": row.get("scope2"),
            "total_emissions": row.get("total_emissions"),
            "carbon_intensity_revenue": row.get("carbon_intensity_revenue"),
            "carbon_change_yoy": row.get("carbon_change_yoy"),
            "revenue": row.get("revenue"),
            "data_source": row.get("data_source") or "clarity",
            "has_carbon_data": bool(row.get("has_carbon_data", True)),
        }
        if existing:
            for key, val in payload.items():
                if val is not None or key in ("has_carbon_data", "data_source"):
                    setattr(existing, key, val)
        else:
            db.session.add(CarbonEmission(symbol=symbol, report_year=year, **payload))
        count += 1
    db.session.commit()
    return count


def sync_market(symbols=None, reason: str = "manual") -> dict:
    """Fetch TradingView quotes/fundamentals and upsert today's snapshot."""
    log = DataSyncLog(
        job_name="market",
        status="running",
        source="tradingview",
        message=reason,
    )
    db.session.add(log)
    db.session.commit()

    if not current_app.config.get("TRADINGVIEW_ENABLED", True):
        return _finish_log(log, "skipped", "disabled", 0, "TRADINGVIEW_ENABLED=false")

    try:
        rows = tradingview_service.fetch_financial_data(
            symbols or universe_symbols(),
            allow_tvkit=(reason != "live-cache"),
        )
        if not rows:
            return _finish_log(
                log,
                "failed",
                "tradingview",
                0,
                "TradingView returned no rows — keeping last database snapshot",
            )
        n = upsert_financial_rows(rows)
        return _finish_log(log, "success", "tradingview", n, reason)
    except Exception as exc:
        logger.exception("Market sync failed")
        db.session.rollback()
        log = DataSyncLog(
            job_name="market",
            status="failed",
            source="tradingview",
            message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
        db.session.add(log)
        db.session.commit()
        return log.to_dict()


def sync_carbon(symbols=None, reason: str = "manual") -> dict:
    """Fetch Clarity AI SFDR carbon metrics and upsert by report year."""
    log = DataSyncLog(
        job_name="carbon",
        status="running",
        source="clarity",
        message=reason,
    )
    db.session.add(log)
    db.session.commit()

    carbon_service.init_app(current_app)
    if not carbon_service.is_configured():
        return _finish_log(
            log,
            "skipped",
            "mock",
            0,
            "CLARITY_API_KEY / CLARITY_API_SECRET not set — mock carbon retained",
        )

    try:
        seed_company_isins()
        rows = carbon_service.fetch_universe_carbon(symbols or universe_symbols())
        if not rows:
            return _finish_log(
                log,
                "failed",
                "clarity",
                0,
                "Clarity returned no rows — keeping last carbon snapshot",
            )
        n = upsert_carbon_rows(rows)
        return _finish_log(log, "success", "clarity", n, reason)
    except Exception as exc:
        logger.exception("Carbon sync failed")
        db.session.rollback()
        log = DataSyncLog(
            job_name="carbon",
            status="failed",
            source="clarity",
            message=str(exc),
            finished_at=datetime.now(timezone.utc),
        )
        db.session.add(log)
        db.session.commit()
        return log.to_dict()


def latest_sync(job_name: str) -> dict | None:
    row = (
        DataSyncLog.query.filter_by(job_name=job_name)
        .order_by(DataSyncLog.started_at.desc())
        .first()
    )
    return row.to_dict() if row else None


_live_lock = threading.Lock()
_last_live_fetch = 0.0


def maybe_refresh_live_quotes() -> dict | None:
    """Refresh today's snapshot if CACHE_TTL has elapsed (screener hot path)."""
    global _last_live_fetch
    app = current_app
    if not app.config.get("TRADINGVIEW_ENABLED", True):
        return None
    if not app.config.get("LIVE_QUOTES", True):
        return None
    ttl = int(app.config.get("CACHE_TTL", 300))
    now = time.time()
    if now - _last_live_fetch < ttl:
        return None
    if not _live_lock.acquire(blocking=False):
        return None
    try:
        if time.time() - _last_live_fetch < ttl:
            return None
        result = sync_market(reason="live-cache")
        _last_live_fetch = time.time()
        return result
    finally:
        _live_lock.release()
