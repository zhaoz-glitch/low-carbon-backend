"""TradingView market data service.

In production this would use the ``tradingview-screener`` Python package
(community wrapper around TradingView's screener API) to batch-fetch
financial metrics like PE, turnover, market cap, etc.

For MVP / development without API access, the service falls back to data
already stored in the local database (seeded by ``mock_data.py``).
"""

import logging

logger = logging.getLogger(__name__)


class TradingViewService:
    """Wrapper around TradingView screener data."""

    def __init__(self, app=None):
        self.app = app
        self._enabled = True
        self._cache = {}  # in-memory cache (replace with Redis in production)

    def init_app(self, app):
        self.app = app
        self._enabled = app.config.get("TRADINGVIEW_ENABLED", True)

    def fetch_financial_data(self, symbols=None, filters=None):
        """Fetch financial metrics for the given symbols.

        Args:
            symbols: list of ticker symbols, or None for all
            filters: dict of TradingView screener filters

        Returns:
            list of dicts with financial metric fields.
        """
        if not self._enabled:
            logger.info("TradingView disabled — using database fallback")
            return None  # caller falls back to DB

        # --- Production implementation (pseudo-code) ---
        # from tradingview_screener import Query
        # q = Query()
        # if symbols:
        #     q.set_filter("name", "in", symbols)
        # if filters:
        #     for field, (op, value) in filters.items():
        #         q.set_filter(field, op, value)
        # columns = ["name", "close", "volume", "market_cap_basic",
        #            "price_earnings_ttm", "price_book_value",
        #            "dividend_yield_recent", "turnover", "change_1_year",
        #            "net_margin"]
        # return q.select(*columns).get_scanner_data()
        #
        # Then cache results in Redis with TTL = CACHE_TTL (5 min)

        logger.info("TradingView fetch not implemented — DB fallback used")
        return None

    def get_market_fields_metadata(self):
        """Return metadata for market/technical filter fields.

        Maps to "Dimension A" in the PRD (real-time / daily updated).
        """
        return [
            {
                "key": "market_cap_basic",
                "label": "市值 (Market Cap)",
                "type": "range",
                "unit": "USD",
                "min": 0,
                "max": 3000000000000,  # 3T
                "step": 100000000,  # 100M
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "turnover",
                "label": "换手率 (Turnover)",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "real-time",
            },
            {
                "key": "price_earnings_ttm",
                "label": "市盈率 (PE TTM)",
                "type": "threshold",
                "unit": "x",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "price_book_value",
                "label": "市净率 (PB)",
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
                "label": "股息率 (Dividend Yield)",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
            {
                "key": "volume",
                "label": "成交量 (Daily Volume)",
                "type": "threshold",
                "unit": "shares",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "real-time",
            },
            {
                "key": "change_1_year",
                "label": "52周涨跌幅 (52-Week Change)",
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
                "label": "净利润率 (Net Profit Margin)",
                "type": "threshold",
                "unit": "%",
                "ops": [">", "<", ">=", "<="],
                "source": "TradingView",
                "update_frequency": "daily",
            },
        ]


# Singleton
tradingview_service = TradingViewService()
