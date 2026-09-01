"""TradingView market + fundamentals service.

Uses the same scanner HTTP API that tvkit wraps
(``https://scanner.tradingview.com``): last price, volume, market cap,
PE TTM, PB, dividend yield, net margin, 1Y change.

During regular hours ``close`` is the last traded price (现价). After the
session it is the official close. We do **not** persist every tick;
``CACHE_TTL`` governs live refresh, and the daily job writes one row per
symbol per calendar date.

tvkit (optional): if installed, ``ScannerService`` is tried first. A direct
REST call with an explicit ticker list is the reliable fallback so our 20
names are always requested rather than scanning the whole US tape.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import requests
from flask import current_app

from app.universe import SAMPLE_COMPANIES, tv_ticker, universe_symbols

logger = logging.getLogger(__name__)

SCANNER_URL = "https://scanner.tradingview.com/america/scan"

# Columns documented by tvkit StockData / ColumnSets.
SCANNER_COLUMNS = [
    "name",
    "close",
    "volume",
    "market_cap_basic",
    "price_earnings_ttm",
    "price_book_fq",
    "dividends_yield_current",
    "net_margin_ttm",
    "Perf.Y",
    "total_revenue_yoy_growth_ttm",
]

_TV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.tradingview.com",
    "Referer": "https://www.tradingview.com/",
}


def _num(value) -> float | None:
    if value is None or value == "" or value == "nan":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def _turnover(close, volume, market_cap) -> float | None:
    close_n = _num(close)
    volume_n = _num(volume)
    cap_n = _num(market_cap)
    if not close_n or not volume_n or not cap_n:
        return None
    return round(volume_n * close_n / cap_n * 100, 4)


def _ticker_to_symbol(raw: str) -> str:
    """NASDAQ:AAPL / AAPL -> AAPL."""
    if not raw:
        return ""
    return raw.split(":")[-1].strip().upper()


class TradingViewService:
    """Batch-fetch quotes and TTM fundamentals from TradingView."""

    def __init__(self, app=None):
        self.app = app
        self._enabled = True

    def init_app(self, app):
        self.app = app
        self._enabled = app.config.get("TRADINGVIEW_ENABLED", True)

    def fetch_financial_data(self, symbols=None, filters=None, allow_tvkit=True):
        """Fetch metrics for *symbols* (default: screener universe).

        Returns a list of dicts ready for ``FinancialMetric`` upsert, or
        ``None`` if TradingView is disabled / the request failed.
        """
        if not self._enabled:
            logger.info("TradingView disabled — using database fallback")
            return None

        wanted = [s.upper() for s in (symbols or universe_symbols())]
        rows = self._fetch_via_http(wanted, quick=not allow_tvkit)
        if rows is None and allow_tvkit:
            rows = self._fetch_via_tvkit(wanted)
        return rows

    def _fetch_via_tvkit(self, symbols: list[str]) -> list[dict] | None:
        """Prefer tvkit ScannerService when the package is installed."""
        try:
            from tvkit.api.scanner import Market, ScannerService
            from tvkit.api.scanner.models.scanner import ScannerRequest, SortConfig
        except ImportError:
            logger.info("tvkit not installed — using scanner REST fallback")
            return None

        tickers = [tv_ticker(s) for s in symbols]
        wanted = set(symbols)

        async def _scan():
            service = ScannerService()
            request = ScannerRequest(
                columns=SCANNER_COLUMNS,
                range=(0, max(len(symbols) * 2, 50)),
                sort=SortConfig(sort_by="market_cap_basic", sort_order="desc"),
                preset="all_stocks",
            )
            # tvkit scans by market; we filter to our universe afterwards.
            response = await service.scan_market(Market.AMERICA, request)
            return list(getattr(response, "data", None) or [])

        try:
            stocks = asyncio.run(_scan())
        except RuntimeError:
            # Nested event loop (e.g. already inside asyncio) — skip tvkit.
            logger.warning("tvkit scanner skipped (event loop already running)")
            return None
        except Exception as exc:
            logger.warning("tvkit scanner failed: %s", exc)
            return None

        mapped = []
        for stock in stocks:
            extra = getattr(stock, "model_extra", None) or getattr(stock, "extra", None) or {}
            raw_name = getattr(stock, "name", None) or extra.get("name")
            symbol = _ticker_to_symbol(str(raw_name or ""))
            if symbol not in wanted:
                continue
            mapped.append(
                self._normalize_row(
                    symbol,
                    {
                        "close": getattr(stock, "close", None),
                        "volume": getattr(stock, "volume", None),
                        "market_cap_basic": getattr(stock, "market_cap_basic", None),
                        "price_earnings_ttm": getattr(stock, "price_earnings_ttm", None),
                        "price_book_fq": getattr(stock, "price_book_fq", None)
                        or extra.get("price_book_fq"),
                        "dividends_yield_current": getattr(stock, "dividends_yield_current", None)
                        or extra.get("dividends_yield_current"),
                        "net_margin_ttm": extra.get("net_margin_ttm"),
                        "Perf.Y": extra.get("Perf.Y"),
                        "total_revenue_yoy_growth_ttm": extra.get("total_revenue_yoy_growth_ttm"),
                    },
                )
            )

        if len(mapped) < max(1, len(symbols) // 2):
            logger.info(
                "tvkit returned %s/%s universe names — falling back to ticker REST",
                len(mapped),
                len(symbols),
            )
            return None

        logger.info("tvkit scanner matched %s symbols", len(mapped))
        return mapped

    def _fetch_via_http(self, symbols: list[str], quick: bool = False) -> list[dict] | None:
        """POST scanner.tradingview.com with an explicit ticker list."""
        exchange_by_symbol = {row[0]: row[4] for row in SAMPLE_COMPANIES}
        tickers = [tv_ticker(s, exchange_by_symbol.get(s)) for s in symbols]
        payload: dict[str, Any] = {
            "symbols": {"tickers": tickers},
            "columns": SCANNER_COLUMNS,
            "range": [0, len(tickers)],
        }
        timeout = 25
        app = self.app
        try:
            app = app or current_app._get_current_object()
        except RuntimeError:
            app = None
        if app is not None:
            timeout = int(app.config.get("TRADINGVIEW_TIMEOUT", 25))
        last_error = None
        body = None
        proxy = ""
        if app is not None:
            proxy = (app.config.get("TRADINGVIEW_PROXY") or "").strip()
        attempts = [(False, None)]
        if proxy:
            attempts.append((False, proxy))
        attempts.append((True, None))
        if quick:
            attempts = [(False, proxy or None)]
        for trust_env, proxy_url in attempts:
            try:
                session = requests.Session()
                session.trust_env = trust_env
                if proxy_url:
                    session.proxies = {"http": proxy_url, "https": proxy_url}
                resp = session.post(
                    SCANNER_URL,
                    json=payload,
                    headers=_TV_HEADERS,
                    timeout=timeout,
                )
                resp.raise_for_status()
                body = resp.json()
                last_error = None
                break
            except requests.RequestException as exc:
                last_error = exc
        if last_error is not None or body is None:
            logger.error("TradingView scanner request failed: %s", last_error)
            return None

        rows = []
        for item in body.get("data") or []:
            raw_ticker = item.get("s") or ""
            symbol = _ticker_to_symbol(raw_ticker)
            values = item.get("d") or []
            fields = dict(zip(SCANNER_COLUMNS, values))
            if not symbol:
                symbol = _ticker_to_symbol(str(fields.get("name") or ""))
            if symbol not in symbols:
                continue
            rows.append(self._normalize_row(symbol, fields))

        logger.info("TradingView REST scanner returned %s symbols", len(rows))
        return rows or None

    def _normalize_row(self, symbol: str, fields: dict) -> dict:
        close = _num(fields.get("close"))
        volume = _num(fields.get("volume"))
        market_cap = _num(fields.get("market_cap_basic"))
        return {
            "symbol": symbol,
            "date": date.today(),
            "close": close,
            "volume": volume,
            "market_cap": market_cap,
            "pe_ttm": _num(fields.get("price_earnings_ttm")),
            "pb": _num(fields.get("price_book_fq") or fields.get("price_book_value")),
            "dividend_yield": _num(fields.get("dividends_yield_current")),
            "turnover": _turnover(close, volume, market_cap),
            "week_52_change": _num(fields.get("Perf.Y") or fields.get("change_1_year")),
            "net_profit_margin": _num(fields.get("net_margin_ttm") or fields.get("net_margin")),
            "revenue_growth": _num(fields.get("total_revenue_yoy_growth_ttm")),
            "data_source": "tradingview",
        }

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


tradingview_service = TradingViewService()
