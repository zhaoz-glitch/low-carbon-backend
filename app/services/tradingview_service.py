"""TradingView market + fundamentals service.

Real data source integration via the ``tradingview-screener`` Python package
(v3.x, sync HTTP wrapper around TradingView's public scanner API — no API key
required).  Field names map 1:1 to TradingView screener columns:

    TradingView column        → FinancialMetric column
    ----------------------------------------------------
    market_cap_basic          → market_cap
    price_earnings_ttm        → pe_ttm
    price_book_fq             → pb
    dividends_yield           → dividend_yield
    net_margin                → net_profit_margin
    total_revenue             → revenue
    close / volume            → close / volume
    Perf.Y                    → week_52_change      (52-week performance %)
    volume / float_shares     → turnover            (derived, see _normalize)

The service is best-effort: any failure (package missing, network error,
TradingView unreachable) falls back to ``None`` so the caller transparently
uses the local database (seeded by ``mock_data.py``).

Retired upstream fields (2026) — verified 0/10 non-null across a sample, so
they are no longer requested:

    price_book_value     → renamed to price_book_fq
    dividend_yield_recent→ renamed to dividends_yield
    turnover             → scanner no longer returns it; derived from
                           volume / float_shares_outstanding instead
    change_1_year        → scanner no longer returns it; replaced by Perf.Y

Docs: https://github.com/shner-elmo/TradingView-Screener
"""

from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger(__name__)

# TradingView screener columns → internal field names (FinancialMetric schema)
TV_COLUMNS = [
    "name",
    "close",
    "volume",
    "market_cap_basic",
    "price_earnings_ttm",
    "price_book_fq",
    "dividends_yield",
    "net_margin",
    "total_revenue",
    "sector",
    "float_shares_outstanding",
    "Perf.Y",
]

COLUMN_MAP = {
    "market_cap_basic": "market_cap",
    "price_earnings_ttm": "pe_ttm",
    "price_book_fq": "pb",
    "dividends_yield": "dividend_yield",
    "net_margin": "net_profit_margin",
    "total_revenue": "revenue",
    "Perf.Y": "week_52_change",
}

# First alias listed is preferred; the rest are fallbacks kept for when the
# scanner starts answering on an older name again.
FIELD_FALLBACKS = {
    "pb": ("price_book_fq", "price_book_value"),
    "dividend_yield": ("dividends_yield", "dividend_yield_recent"),
    "week_52_change": ("Perf.Y", "change_1_year"),
}

# Legacy frontend/API filter keys → current TradingView scanner columns.
# TradingView renamed the underlying fields; keep old keys working.
TV_FIELD_ALIASES = {
    "price_book_value": "price_book_fq",
    "dividend_yield_recent": "dividends_yield",
}


class TradingViewService:
    """Wrapper around TradingView screener data (real integration)."""

    def __init__(self, app=None):
        self.app = app
        self._enabled = True
        self._cache = {}  # in-memory cache: {"data": [...], "ts": epoch}
        self._cache_ttl = 300  # 5 minutes (matches PRD cache spec)

    def init_app(self, app):
        self.app = app
        self._enabled = app.config.get("TRADINGVIEW_ENABLED", True)
        self._cache_ttl = app.config.get("CACHE_TTL", 300)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_financial_data(self, symbols=None, filters=None, types="stock"):
        """Fetch metrics for *symbols*, or the whole US market when omitted.

        Args:
            symbols: list of ticker symbols, or None for the full US market
            filters: optional dict of TradingView screener filters
                     (column → (op, value))
            types: symbol type for the full-market scan (``stock`` / ``all``)

        Returns:
            list of dicts with FinancialMetric-schema fields,
            or None on any failure (caller falls back to DB).
        """
        if not self._enabled:
            logger.info("TradingView disabled — using database fallback")
            return None

        # Cache is only used for full-universe fetches (no symbol subset)
        cache_key = "all" if not symbols else None
        if cache_key and self._cache.get(cache_key):
            age = time.time() - self._cache[cache_key]["ts"]
            if age < self._cache_ttl:
                return self._cache[cache_key]["data"]

        try:
            if symbols:
                rows = self._query_scanner(symbols, filters)
            else:
                rows = self._query_universe(types)
        except ImportError:
            logger.warning(
                "tradingview-screener not installed — DB fallback used. "
                "Install with: pip install tradingview-screener"
            )
            return None
        except Exception as e:  # noqa: BLE001 — any upstream error → fallback
            logger.error("TradingView query failed: %s", e)
            return None

        data = [self._normalize(r) for r in rows if r.get("name")]
        if cache_key:
            self._cache[cache_key] = {"data": data, "ts": time.time()}
        logger.info("TradingView fetched %d rows", len(data))
        return data

    def fetch_and_store(self, symbols=None):
        """Fetch from TradingView and upsert into ``financial_metrics``.

        Thin delegate to the single write path — this class only knows how to
        *read* the upstream feed, never how to persist it.

        Returns number of rows upserted, or None if the fetch failed.
        """
        data = self.fetch_financial_data(symbols=symbols)
        if data is None:
            return None
        from app.services.market_snapshot_service import market_snapshot_service

        return market_snapshot_service.upsert(data, source="tradingview")

    def get_market_fields_metadata(self):
        """Return metadata for market/technical filter fields (Dimension A)."""
        return [
            {
                "key": "market_cap_basic",
                "label": "Market Cap",
                "type": "range",
                "unit": "USD",
                "min": 0,
                "max": 3000000000000,
                "step": 100000000,
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "turnover",
                "label": "Turnover",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "real-time",
            },
            {
                "key": "price_earnings_ttm",
                "label": "PE (TTM)",
                "type": "threshold",
                "unit": "x",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "price_book_value",
                "label": "PB",
                "type": "range",
                "unit": "x",
                "min": 0,
                "max": 50,
                "step": 0.5,
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "dividend_yield_recent",
                "label": "Dividend Yield",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "volume",
                "label": "Daily Volume",
                "type": "threshold",
                "unit": "shares",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "real-time",
            },
            {
                "key": "change_1_year",
                "label": "52-Week Change",
                "type": "range",
                "unit": "%",
                "min": -100,
                "max": 200,
                "step": 1,
                "source": "TradingView",
                "update_frequency": "real-time",
            },
            {
                "key": "net_margin",
                "label": "Net Profit Margin",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_scanner(self, symbols, filters):
        """Run a TradingView screener query. Raises ImportError if the
        package is not installed, and propagates upstream errors.

        Uses the tradingview-screener 3.x API (set_markets / where / col).
        Note: bare tickers ("AAPL") do NOT work with set_tickers in 3.x —
        it requires "NASDAQ:AAPL" format — so bare symbols are filtered
        via ``where(col('name').isin([...]))`` instead.
        """
        from tradingview_screener import Query, col

        q = Query().select(*TV_COLUMNS).set_markets("america")
        if symbols:
            q = q.where(col("name").isin(list(symbols)))
        else:
            # Default universe: large caps with known market cap
            q = q.where(col("market_cap_basic") > 1_000_000_000).limit(1000)
        if filters:
            # filters: {column: (op, value)} with op in {>, <, >=, <=}
            import operator

            # Legacy frontend keys → current TradingView column names
            tv_field = TV_FIELD_ALIASES.get

            ops = {
                ">": operator.gt,
                "<": operator.lt,
                ">=": operator.ge,
                "<=": operator.le,
            }
            exprs = [
                ops[op](col(tv_field(column, column)), value)
                for column, (op, value) in filters.items()
                if op in ops
            ]
            if exprs:
                q = q.where(*exprs)

        _, df = q.get_scanner_data()
        return df.to_dict("records")

    def _query_universe(self, types="stock"):
        """Scan the entire US market (used by the daily ETL and live refresh)."""
        from tradingview_screener import Query, col

        q = (
            Query()
            .select(*TV_COLUMNS, "description", "industry", "exchange", "type")
            .set_markets("america")
            .where()
            .limit(100_000)
        )
        if types != "all":
            q = q.where(col("type") == types)
        total, df = q.get_scanner_data()
        logger.info("TradingView universe scan: %s symbols (type=%s)", total, types)
        return df.to_dict("records")

    @staticmethod
    def _num(value):
        """NaN / pandas NA → None so nothing invalid reaches the database."""
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return value

    @classmethod
    def _normalize(cls, row):
        """Map a raw TradingView row to the FinancialMetric schema."""
        pick = lambda *names: next(  # noqa: E731 — first non-null alias wins
            (cls._num(row.get(n)) for n in names if cls._num(row.get(n)) is not None),
            None,
        )

        # Identity fields ride along so the daily ETL can maintain the
        # ``companies`` table from the same scan (the snapshot service ignores
        # anything outside MERGE_FIELDS).
        out = {
            "symbol": row.get("name"),
            "name": cls._num(row.get("description")),
            "sector": cls._num(row.get("sector")),
            "industry": cls._num(row.get("industry")),
            "exchange": cls._num(row.get("exchange")),
        }
        for col, field in COLUMN_MAP.items():
            if col == "Perf.Y":
                continue  # handled below via FIELD_FALLBACKS
            out[field] = cls._num(row.get(col))

        # Direct 1:1 columns
        out["close"] = cls._num(row.get("close"))
        out["volume"] = cls._num(row.get("volume"))

        # Aliased columns (rename-tolerant)
        for field, names in FIELD_FALLBACKS.items():
            out[field] = pick(*names)

        # Derived: turnover rate (%) = shares traded / free float.
        # The scanner dropped its own ``turnover`` column in 2026.
        volume = out.get("volume")
        free_float = cls._num(row.get("float_shares_outstanding"))
        out["turnover"] = (
            round(float(volume) / float(free_float) * 100, 4)
            if volume is not None and free_float
            else None
        )
        return out


tradingview_service = TradingViewService()
