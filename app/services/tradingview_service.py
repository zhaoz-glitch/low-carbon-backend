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
    turnover                  → turnover
    change_1_year             → week_52_change
    net_margin                → net_profit_margin
    close / volume            → close / volume

The service is best-effort: any failure (package missing, network error,
TradingView unreachable) falls back to ``None`` so the caller transparently
uses the local database (seeded by ``mock_data.py``).

Note: TradingView renamed scanner fields in 2026 — ``price_book_value`` →
``price_book_fq`` and ``dividend_yield_recent`` → ``dividends_yield``.
The old names now return null, so only the new names are used below.

Docs: https://github.com/shner-elmo/TradingView-Screener
"""

from __future__ import annotations

import asyncio
import logging
import time

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
    "turnover",
    "change_1_year",
    "net_margin",
    "total_revenue",
    "sector",
]

COLUMN_MAP = {
    "market_cap_basic": "market_cap",
    "price_earnings_ttm": "pe_ttm",
    "price_book_fq": "pb",
    "dividends_yield": "dividend_yield",
    "change_1_year": "week_52_change",
    "net_margin": "net_profit_margin",
    "total_revenue": "revenue",
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

    def fetch_financial_data(self, symbols=None, filters=None, allow_tvkit=True):
        """Fetch metrics for *symbols* (default: screener universe).

        Args:
            symbols: list of ticker symbols, or None for the default universe
            filters: optional dict of TradingView screener filters
                     (column → (op, value))

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
            rows = self._query_scanner(symbols, filters)
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

    def fetch_and_store(self, symbols=None, db=None):
        """Fetch from TradingView and upsert into ``financial_metrics``.

        Returns number of rows upserted, or None if fetch failed.
        """
        if db is None and self.app is not None:
            from app.extensions import db as _db

            db = _db

        data = self.fetch_financial_data(symbols=symbols)
        if data is None:
            return None

        from app.models.company import Company
        from app.models.financial_metric import FinancialMetric
        from datetime import date

        today = date.today()
        count = 0
        for row in data:
            symbol = row.get("symbol")
            if not symbol:
                continue
            # Only upsert companies known in our DB (keeps FK valid)
            if not Company.query.filter_by(symbol=symbol).first():
                continue
            existing = FinancialMetric.query.filter_by(
                symbol=symbol, date=today
            ).first()
            if existing:
                for key, val in row.items():
                    if key not in ("symbol", "date") and val is not None:
                        setattr(existing, key, val)
            else:
                db.session.add(
                    FinancialMetric(symbol=symbol, date=today, **{
                        k: v for k, v in row.items()
                        if k not in ("symbol", "date", "sector")
                    })
                )
            count += 1

        db.session.commit()
        logger.info("Upserted %d financial_metrics rows", count)
        return count

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

    @staticmethod
    def _normalize(row):
        """Map a raw TradingView row to the FinancialMetric schema."""
        out = {"symbol": row.get("name"), "sector": row.get("sector")}
        for col, field in COLUMN_MAP.items():
            out[field] = row.get(col)
        # Direct 1:1 columns
        for col in ("close", "volume", "turnover"):
            out[col] = row.get(col)
        return out


tradingview_service = TradingViewService()
