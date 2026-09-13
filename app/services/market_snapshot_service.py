"""Single write path for ``financial_metrics``.

Every producer of market data goes through :meth:`MarketSnapshotService.upsert`:

    scripts/daily_etl.py            full US-market scan (once a day)
    app/jobs/sync.py::sync_market   live-quote refresh / manual / cron
    scripts/carbon_etl.py           revenue + revenue_growth only

Two guarantees this service provides, both of which used to be violated by the
four hand-rolled upsert loops scattered through the codebase:

1. **One row per company.**  ``financial_metrics`` is keyed by ``symbol`` alone
   (``uq_financial_metrics_symbol``), so repeated runs update the same row
   instead of appending a new dated row each day.

2. **Partial payloads never blank out richer ones.**  Upstream feeds are
   uneven — TradingView's live scanner returns ``close``/``market_cap`` for
   every symbol but drops other columns for some.  A ``None`` in the incoming
   payload is treated as "no new information" and the stored value is kept.
   That is why a stripped-down live refresh can no longer wipe the fields a
   full daily scan had already populated.

Note the merge semantics also settle the carbon/market overlap: the carbon ETL
only ever writes ``revenue`` and ``revenue_growth``, and those survive a
subsequent market sync because the market payload has them as ``None``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import pandas as pd
from sqlalchemy import func

from app.extensions import db
from app.models.company import Company
from app.models.financial_metric import FinancialMetric

logger = logging.getLogger(__name__)

# Fields owned by the market feed. ``data_source`` tracks whichever producer
# last touched one of these — a carbon-only write must not relabel the row.
MARKET_FIELDS = (
    "close",
    "volume",
    "market_cap",
    "pe_ttm",
    "pb",
    "dividend_yield",
    "turnover",
    "week_52_change",
    "net_profit_margin",
)

# Everything a producer may contribute. ``symbol`` is the key and is never
# overwritten; ``as_of_date`` is handled separately (see ``upsert``).
MERGE_FIELDS = MARKET_FIELDS + (
    "revenue_growth",
    "revenue",
)


def _clean(value):
    """Drop NaN / pandas NA / empty strings so they are not written as values."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _as_date(value):
    """Normalise a date-ish value to ``datetime.date`` (or None)."""
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


class MarketSnapshotService:
    """Owner of every write into ``financial_metrics``."""

    def upsert(self, rows, source: str = "tradingview", as_of=None) -> int:
        """Upsert a batch of snapshot rows, one per symbol.

        Args:
            rows: iterable of dicts using ``FinancialMetric`` field names.
                Unknown field keys are ignored; ``None`` values mean
                "leave whatever is already stored".
            source: value written to ``data_source`` (e.g. ``daily_etl``,
                ``live-cache``, ``carbon-etl``).
            as_of: fallback ``as_of_date`` for rows that do not carry one.

        Returns:
            Number of symbols written.
        """
        prepared = {}
        for row in rows:
            symbol = _clean(row.get("symbol"))
            if not symbol:
                continue
            fields = {f: _clean(row.get(f)) for f in MERGE_FIELDS}
            prepared[str(symbol).upper()] = {
                "fields": fields,
                # Only a producer that actually knows the market date may move
                # it; the carbon ETL must not stamp today onto the snapshot.
                "as_of_date": _as_date(row.get("as_of_date")) or _as_date(as_of),
                "touches_market": any(fields[f] is not None for f in MARKET_FIELDS),
            }

        if not prepared:
            return 0

        symbols = list(prepared)
        known = {
            s for (s,) in db.session.query(Company.symbol)
            .filter(Company.symbol.in_(symbols))
            .all()
        }
        missing = [s for s in symbols if s not in known]
        if missing:
            logger.info(
                "market snapshot: skipped %d unknown symbol(s): %s",
                len(missing), missing[:5],
            )

        existing = {
            fm.symbol: fm
            for fm in FinancialMetric.query
            .filter(FinancialMetric.symbol.in_(known))
            .all()
        }

        written = 0
        for symbol in symbols:
            if symbol not in known:
                continue
            entry = prepared[symbol]
            fields, row_as_of = entry["fields"], entry["as_of_date"]
            current = existing.get(symbol)
            if current is None:
                db.session.add(
                    FinancialMetric(
                        symbol=symbol,
                        as_of_date=row_as_of or date.today(),
                        data_source=source,
                        **{k: v for k, v in fields.items() if v is not None},
                    )
                )
            else:
                changed = False
                for key, value in fields.items():
                    if value is not None and getattr(current, key) != value:
                        setattr(current, key, value)
                        changed = True
                if row_as_of is not None and current.as_of_date != row_as_of:
                    current.as_of_date = row_as_of
                    changed = True
                if changed:
                    if entry["touches_market"]:
                        current.data_source = source
                    current.updated_at = datetime.now(timezone.utc)
            written += 1

        db.session.commit()
        logger.info(
            "market snapshot: %d symbols written (source=%s, universe=%d)",
            written, source, len(symbols),
        )
        return written

    def upsert_one(self, symbol: str, source: str = "manual", **fields) -> bool:
        """Convenience wrapper for single-symbol updates (carbon revenue, …)."""
        payload = dict(fields, symbol=symbol)
        return bool(self.upsert([payload], source=source))

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def coverage(self) -> dict:
        """Report how complete the current snapshot set is.

        Used by the admin endpoint to prove the refactor worked: every symbol
        should have exactly one row, and the key columns should be populated.
        """
        total = db.session.query(func.count()).select_from(FinancialMetric).scalar() or 0
        distinct = (
            db.session.query(func.count(func.distinct(FinancialMetric.symbol))).scalar()
            or 0
        )

        def filled(column):
            return (
                db.session.query(func.count())
                .select_from(FinancialMetric)
                .filter(column.isnot(None))
                .scalar()
                or 0
            )

        rates = {
            name: filled(getattr(FinancialMetric, name))
            for name in (
                "close", "volume", "market_cap", "pe_ttm", "pb",
                "dividend_yield", "turnover", "week_52_change",
                "net_profit_margin", "revenue_growth", "revenue",
            )
        }
        return {
            "rows": total,
            "distinct_symbols": distinct,
            "duplicate_rows": total - distinct,
            "filled": rates,
            "fill_pct": {
                k: round(v / total * 100, 1) if total else 0.0
                for k, v in rates.items()
            },
        }


market_snapshot_service = MarketSnapshotService()
