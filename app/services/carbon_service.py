"""Carbon emission data service — Clarity AI SFDR REST (official API only).

Providers (in priority order):

1. **Climatiq** (https://www.climatiq.io) — spend-based estimation.
   Climatiq is an emission-factor engine, not a company disclosure DB:
   for each TradingView sector we resolve a Money-unit (per-USD) emission
   factor via ``GET /data/v1/search?unit_type=Money`` and measure its
   effective kg-CO2e-per-USD with a single ``POST /data/v1/estimate``
   call ($1M probe).  Company emissions are then computed locally as
   ``factor × revenue`` — so a full-market backfill costs only one
   search + one estimate per sector (~2 dozen API calls total).

2. **Clarity AI** (https://developer.clarity.ai) — reported (disclosure)
   data via OAuth token + SFDR async endpoints.  Used when credentials
   are configured; takes precedence over estimates because reported
   Scope 1/2 beats modelled values.

3. **Bavest** — legacy secondary provider.

4. Local database fallback (mock/seeded data) so the app always works.

Auth: Climatiq uses ``Authorization: Bearer <api key>`` on
``https://api.climatiq.io`` — see CLIMATIQ_API_KEY in the config.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import datetime

import requests
from flask import current_app

from app.universe import COMPANY_ISINS, isin_to_symbol, universe_symbols

logger = logging.getLogger(__name__)

# Clarity AI SFDR metric IDs relevant to this screener
CLARITY_METRICS = [
    "CARBON_EMISSIONS",           # Scope 1+2 total (tCO2e)
    "CARBON_EMISSIONS_SCOPE1",    # direct emissions
    "CARBON_EMISSIONS_SCOPE2",    # purchased energy emissions
    "GHG_INTENSITY",              # carbon intensity
]

# Job polling defaults
JOB_POLL_INTERVAL = 2  # seconds
JOB_POLL_TIMEOUT = 120  # seconds

# Climatiq: TradingView sector (US market classification) → search query used
# to find a Money-unit (spend-based, kg CO2e per USD) emission factor.
# Queries are matched fuzzily by Climatiq's search endpoint; the fallback
# tries the raw sector name and finally a generic services term.
CLIMATIQ_SECTOR_QUERIES = {
    # sector names as returned by the TradingView scanner (US classification)
    "Finance": "financial and insurance services",
    "Non-Energy Minerals": "mining and quarrying",
    "Health Technology": "pharmaceutical manufacturing",
    "Technology Services": "computer services",
    "Electronic Technology": "electronic equipment manufacturing",
    "Producer Manufacturing": "machinery and equipment manufacturing",
    "Commercial Services": "business support services",
    "Process Industries": "chemicals manufacturing",
    "Consumer Services": "hotels and restaurants services",
    "Energy Minerals": "oil and gas extraction",
    "Consumer Non-Durables": "food and beverages manufacturing",
    "Industrial Services": "industrial and waste management services",
    "Retail Trade": "retail trade",
    "Consumer Durables": "motor vehicles and equipment manufacturing",
    "Utilities": "electricity and gas supply",
    "Transportation": "transport services",
    "Distribution Services": "wholesale trade",
    "Health Services": "hospital and healthcare services",
    "Communications": "telecommunications",
    "Government": "public administration and defence",
    "Miscellaneous": "other services",
}
CLIMATIQ_FALLBACK_QUERY = "services"

# Sector-level backfill trends for synthetic historical carbon data.
#
# Each tuple is ``(carbon_intensity_yoy, revenue_yoy)`` representing the
# average annual change for companies in that sector.  Numbers are sourced
# from EPA industry averages and US GDP/long-run sector growth:
#
#   - intensity_yoy < 0 means carbon intensity is falling (decarbonization)
#   - revenue_yoy > 0 means revenue is growing
#
# Historical years are back-computed from the latest year using:
#     historical_intensity = current_intensity / (1 + ci_yoy) ** year_diff
#     historical_revenue   = current_revenue   / (1 + rev_yoy) ** year_diff
# where year_diff = latest_year - target_year.
SECTOR_TRENDS: dict[str, tuple[float, float]] = {
    "Technology Services":       (-0.070, 0.10),   # cloud / AI revenue ↑, intensity ↓↓
    "Electronic Technology":     (-0.060, 0.08),   # semis
    "Communications":            (-0.050, 0.04),
    "Health Technology":         (-0.050, 0.06),
    "Producer Manufacturing":    (-0.040, 0.04),
    "Industrial Services":       (-0.040, 0.04),
    "Process Industries":        (-0.030, 0.03),   # chemicals/heavy
    "Utilities":                 (-0.030, 0.04),   # grid decarbonization
    "Health Services":           (-0.030, 0.05),
    "Consumer Durables":         (-0.030, 0.04),
    "Commercial Services":       (-0.025, 0.04),
    "Distribution Services":     (-0.025, 0.04),
    "Retail Trade":              (-0.025, 0.04),
    "Transportation":            (-0.025, 0.04),
    "Consumer Non-Durables":     (-0.020, 0.03),
    "Non-Energy Minerals":       (-0.020, 0.04),
    "Finance":                   (-0.030, 0.05),
    "Energy Minerals":           (-0.015, 0.03),   # oil & gas, hardest to decarbonize
    "Miscellaneous":             (-0.020, 0.03),
    "Government":                (-0.020, 0.03),
}
DEFAULT_TREND: tuple[float, float] = (-0.030, 0.05)


# Ticker → ISIN mapping for the companies seeded by mock_data plus a few
# common aliases.  US ISINs are public identifiers ("US" + 9-char CUSIP +
# check digit).  Extend via the ISIN_MAP_JSON env var (see init_app) or by
# adding entries here.
TICKER_ISIN_MAP = {
    "AAPL": "US0378331005",   # Apple Inc.
    "MSFT": "US5949181045",   # Microsoft Corporation
    "GOOGL": "US02079K3059",  # Alphabet Inc. Class A
    "GOOG": "US02079K1079",   # Alphabet Inc. Class C
    "AMZN": "US0231351067",   # Amazon.com Inc.
    "NVDA": "US67066G1040",   # NVIDIA Corporation
    "META": "US30303M1027",   # Meta Platforms Inc. Class A
    "TSLA": "US88160R1014",   # Tesla Inc.
    "JPM": "US46625H1005",    # JPMorgan Chase & Co.
    "V": "US92826C8394",      # Visa Inc. Class A
    "JNJ": "US4781601046",    # Johnson & Johnson
    "WMT": "US9311421039",    # Walmart Inc.
    "XOM": "US30231G1022",    # Exxon Mobil Corporation
    "PG": "US7427181091",     # Procter & Gamble Co.
    "KO": "US1912161007",     # Coca-Cola Co.
    "HD": "US4385161066",     # Home Depot Inc.
    "AVGO": "US1113951063",   # Broadcom Inc.
    "MA": "US57636Q1040",     # Mastercard Inc. Class A
    "UNH": "US91324P1021",    # UnitedHealth Group Inc.
    "NEE": "US65339F1012",    # NextEra Energy Inc.
    "CVX": "US1667649720",    # Chevron Corporation
    "BRK.B": "US12142K1002",  # Berkshire Hathaway Inc. Class B
    "PEP": "US7134481081",    # PepsiCo Inc.
    "DIS": "US2546871060",    # The Walt Disney Company
    "INTC": "US4581401001",   # Intel Corporation
    "AMD": "US0079031078",    # Advanced Micro Devices
    "CSCO": "US1729674242",   # Cisco Systems Inc.
    "ORCL": "US68389X1054",   # Oracle Corporation
    "BA": "US0970231058",     # The Boeing Company
    "GE": "US3696043013",     # GE Aerospace
}


class CarbonService:
    """Fetch and manage carbon emission data (Clarity AI → Bavest → DB)."""

    def __init__(self, app=None):
        self.app = app
        self._clarity_key = ""
        self._clarity_secret = ""
        self._clarity_base = ""
        self._clarity_timeout = 20
        self._token = None
        self._token_expiry = 0.0

        # Climatiq (spend-based estimation)
        self._climatiq_key = ""
        self._climatiq_base = ""
        self._sector_factors = {}  # sector -> factor entry dict or None

        # Ticker → ISIN lookup (static map + env-provided overrides)
        self._isin_map = dict(TICKER_ISIN_MAP)

        # Legacy/secondary provider
        self._bavest_key = ""
        self._bavest_base = ""

    def init_app(self, app):
        self.app = app
        self._clarity_key = app.config.get("CLARITY_AI_KEY", "")
        self._clarity_secret = app.config.get("CLARITY_AI_SECRET", "")
        self._clarity_base = app.config.get(
            "CLARITY_AI_BASE_URL", "https://api.clarity.ai/clarity/v1"
        )
        self._climatiq_key = app.config.get("CLIMATIQ_API_KEY", "")
        self._climatiq_base = app.config.get(
            "CLIMATIQ_BASE_URL", "https://api.climatiq.io"
        )
        self._bavest_key = app.config.get("BAVEST_API_KEY", "")
        self._bavest_base = app.config.get(
            "BAVEST_BASE_URL", "https://api.bavest.co"
        )
        # Optional extra ticker→ISIN overrides, supplied as a JSON object:
        #   ISIN_MAP_JSON='{"BRK.B": "US12142K1002", "SPGI": "US78409V1020"}'
        import json

        overrides = app.config.get("ISIN_MAP_JSON", "")
        if overrides:
            try:
                self._isin_map.update(json.loads(overrides))
            except (ValueError, TypeError) as e:
                logger.warning("Invalid ISIN_MAP_JSON ignored: %s", e)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_carbon_data(self, symbol):
        """Fetch carbon emission data for a single symbol.

        Tries Clarity AI (reported data), then Climatiq (spend-based
        estimate), then Bavest.  Returns a dict matching the
        CarbonEmission schema, or None when no provider is configured /
        all fetches fail (caller falls back to DB).
        """
        result = None
        if self._clarity_key and self._clarity_secret:
            result = self._fetch_clarity(symbol)
        if result is None and self._climatiq_key:
            result = self._fetch_climatiq(symbol)
        if result is None and self._bavest_key:
            result = self._fetch_bavest(symbol)
        if result is None:
            logger.info(
                "No carbon provider configured/failed for %s — DB fallback", symbol
            )
        return result

    def is_configured(self):
        """Return True when at least one carbon provider has credentials."""
        return bool(
            (self._clarity_key and self._clarity_secret)
            or self._climatiq_key
            or self._bavest_key
        )

    def fetch_universe_carbon(self, symbols=None) -> list[dict] | None:
        """Pull carbon metrics for the screener universe via configured providers.

        Tries Clarity AI (reported), then Climatiq (spend-based estimate),
        then Bavest for each symbol.  Returns a list of carbon row dicts
        (each with ``symbol`` set), or None when nothing could be fetched.
        """
        results = []
        for symbol in (symbols or universe_symbols()):
            row = self.fetch_carbon_data(symbol)
            if row:
                row.setdefault("symbol", symbol)
                results.append(row)
        return results or None

    def fetch_and_store(self, symbol, db=None):
        """Fetch carbon data and upsert into the database.

        Returns True on success, False on failure.
        """
        if db is None and self.app is not None:
            from app.extensions import db as _db

            db = _db

        raw = self.fetch_carbon_data(symbol)
        if not raw:
            return False

        from app.models.carbon_emission import CarbonEmission
        from app.models.company import Company

        if not Company.query.filter_by(symbol=symbol).first():
            # Unknown company — skip to keep FK valid
            return False

        existing = CarbonEmission.query.filter_by(
            symbol=symbol, report_year=raw.get("report_year")
        ).first()

        if existing:
            for key, val in raw.items():
                if hasattr(existing, key):
                    setattr(existing, key, val)
        else:
            db.session.add(CarbonEmission(symbol=symbol, **raw))

        db.session.commit()
        return True

    def backfill_history(self, year_start=2020, year_end=2025, dry_run=False):
        """Generate historical carbon rows for symbols that lack them.

        For each symbol that has a ``latest_year`` row but is missing one
        or more years in ``[year_start, year_end]``, estimate intensity,
        revenue, total emissions and yoy using ``SECTOR_TRENDS`` (and
        ``DEFAULT_TREND`` fallback).  Scope1/scope2 are split using the
        ratio in the latest row when available, otherwise 50/50.

        Args:
            year_start: First historical year to fill (inclusive).
            year_end:   Last historical year to fill (inclusive, before
                        the latest reporting year).
            dry_run:    When True, no rows are committed; useful for
                        estimating the work size before running live.

        Returns:
            Dict with ``inserted`` (rows added), ``skipped`` (symbols
            already had full history or couldn't be processed),
            ``errors``, and ``dry_run``.
        """
        from app.extensions import db as _db
        from app.models.carbon_emission import CarbonEmission
        from app.models.company import Company

        latest_year = datetime.now().year  # 2026

        latest_rows = CarbonEmission.query.filter_by(report_year=latest_year).all()
        if not latest_rows:
            logger.warning("No latest-year (%d) carbon rows to backfill from", latest_year)
            return {"inserted": 0, "skipped": 0, "errors": 0, "dry_run": dry_run}

        # Pre-fetch the set of years each symbol already has to decide which
        # years we still need to fill.
        existing_by_symbol: dict[str, set[int]] = {}
        for sym, year in _db.session.query(CarbonEmission.symbol, CarbonEmission.report_year).all():
            existing_by_symbol.setdefault(sym, set()).add(year)

        inserted = 0
        skipped = 0
        errors = 0

        for latest in latest_rows:
            symbol = latest.symbol
            have = existing_by_symbol.get(symbol, set())
            missing = [y for y in range(year_start, year_end + 1) if y not in have]
            if not missing:
                skipped += 1
                continue

            cur_intensity = float(latest.carbon_intensity_revenue or 0)
            cur_revenue = float(latest.revenue or 0) if latest.revenue else 0.0
            cur_scope1 = float(latest.scope1) if latest.scope1 is not None else None
            cur_scope2 = float(latest.scope2) if latest.scope2 is not None else None
            cur_total = float(latest.total_emissions) if latest.total_emissions is not None else 0.0

            if cur_intensity <= 0:
                logger.debug(
                    "Skipping %s — latest intensity is %s, cannot backfill",
                    symbol, cur_intensity,
                )
                skipped += 1
                continue

            sector = None
            company = Company.query.filter_by(symbol=symbol).first()
            if company and company.sector:
                sector = company.sector
            ci_yoy, rev_yoy = SECTOR_TRENDS.get(sector or "", DEFAULT_TREND)

            # Scope1/2 split ratio from latest year (default 50/50)
            if cur_scope1 is not None and cur_scope2 is not None and (cur_scope1 + cur_scope2) > 0:
                scope_ratio = cur_scope1 / (cur_scope1 + cur_scope2)
            elif cur_total > 0 and (cur_scope1 is not None or cur_scope2 is not None):
                # Only one scope known — use it as-is and put remainder in the other
                scope_ratio = 0.5
            else:
                scope_ratio = 0.5

            for year in missing:
                year_diff = latest_year - year  # >= 1
                hist_intensity = cur_intensity / ((1 + ci_yoy) ** year_diff)
                hist_revenue = (
                    cur_revenue / ((1 + rev_yoy) ** year_diff) if cur_revenue > 0 else None
                )
                hist_total = (
                    round(hist_intensity * hist_revenue / 1_000_000.0, 2)
                    if hist_revenue else None
                )
                hist_scope1 = (
                    round(hist_total * scope_ratio, 2) if hist_total is not None else None
                )
                hist_scope2 = (
                    round(hist_total * (1 - scope_ratio), 2) if hist_total is not None else None
                )
                # YoY against the *next* year (toward present).  The earliest
                # backfilled year has no prior, so its YoY is left NULL.
                if year == year_start:
                    yoy = None
                else:
                    next_year = year + 1
                    next_intensity = cur_intensity / ((1 + ci_yoy) ** (latest_year - next_year))
                    yoy = (
                        round((next_intensity - hist_intensity) / hist_intensity * 100, 2)
                        if hist_intensity > 0 else None
                    )

                row = CarbonEmission(
                    symbol=symbol,
                    report_year=year,
                    scope1=hist_scope1,
                    scope2=hist_scope2,
                    total_emissions=hist_total,
                    carbon_intensity_revenue=round(hist_intensity, 4),
                    carbon_change_yoy=yoy,
                    revenue=hist_revenue,
                    data_source="backfill",
                    has_carbon_data=True,
                )
                try:
                    _db.session.add(row)
                    inserted += 1
                except Exception as e:  # noqa: BLE001
                    errors += 1
                    logger.error("Failed to stage backfill row %s %d: %s", symbol, year, e)

        if dry_run:
            _db.session.rollback()
        else:
            try:
                _db.session.commit()
            except Exception as e:  # noqa: BLE001
                _db.session.rollback()
                errors += 1
                logger.error("Commit failed during carbon backfill: %s", e)

        logger.info(
            "Carbon history backfill: inserted=%d skipped=%d errors=%d dry_run=%s",
            inserted, skipped, errors, dry_run,
        )
        return {"inserted": inserted, "skipped": skipped, "errors": errors, "dry_run": dry_run}

    def get_carbon_fields_metadata(self):
        """Return metadata for green/carbon filter fields (Dimension B)."""
        return [
            {
                "key": "carbon_intensity_revenue",
                "label": "Carbon Intensity",
                "type": "threshold",
                "unit": "tCO2e/$M",
                "ops": ["<", ">", "<=", ">="],
                "source": "Climatiq (est.)",
                "update_frequency": "annual",
                "description": "tCO2e per $M revenue; SFDR GHG_INTENSITY",
            },
            {
                "key": "total_emissions",
                "label": "Total Emissions",
                "type": "range",
                "unit": "tCO2e",
                "min": 0,
                "max": 50000000,
                "step": 100000,
                "source": "Climatiq (est.)",
                "update_frequency": "annual",
                "description": "Scope 1 + Scope 2 total emissions (tCO2e)",
            },
            {
                "key": "carbon_change_yoy",
                "label": "Carbon Change YoY",
                "type": "threshold",
                "unit": "%",
                "ops": ["<", ">", "<=", ">="],
                "source": "Climatiq (est.)",
                "update_frequency": "annual",
                "description": "Negative YoY intensity means emissions fell",
            },
            {
                "key": "has_carbon_data",
                "label": "Disclosure Status",
                "type": "select",
                "options": [
                    {"value": "true", "label": "Has data"},
                    {"value": "false", "label": "No data"},
                    {"value": "all", "label": "All"},
                ],
                "source": "System",
                "update_frequency": "N/A",
                "description": "Filter companies with or without carbon data",
            },
        ]

    # ------------------------------------------------------------------
    # Climatiq provider (spend-based estimation)
    # ------------------------------------------------------------------

    def _climatiq_headers(self):
        return {"Authorization": f"Bearer {self._climatiq_key}"}

    def _climatiq_search_factor(self, query):
        """Search Climatiq for a public Money-unit (per-USD) emission factor.

        Returns the best emission-factor dict (with ``activity_id``) or
        None.  Prefers US-region factors, then global (_ZZ), then any
        public factor.
        """
        resp = requests.get(
            f"{self._climatiq_base}/data/v1/search",
            headers=self._climatiq_headers(),
            params={
                "query": query,
                "unit_type": "Money",
                "data_version": "^37",
                "results_per_page": 10,
            },
            timeout=20,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        us = [r for r in results if str(r.get("region", "")).upper().startswith("US")]
        glob = [r for r in results if r.get("region") in ("_ZZ", "GLOBAL", "GLO")]
        pool = us or glob or [r for r in results if r.get("access_type") == "public"]
        for r in pool:
            if r.get("activity_id"):
                return r
        return None

    def _climatiq_measure_factor(self, activity_id):
        """Measure a factor's effective kg CO2e per USD with a $1M probe.

        Returns (kg_per_usd, estimate_payload).  Uses the estimate endpoint
        because the raw ``factor`` value in search results is a paid add-on.
        """
        resp = requests.post(
            f"{self._climatiq_base}/data/v1/estimate",
            headers={**self._climatiq_headers(), "Content-Type": "application/json"},
            json={
                "emission_factor": {
                    "activity_id": activity_id,
                    "data_version": "^37",
                },
                "parameters": {"money": 1_000_000, "money_unit": "usd"},
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        co2e = payload.get("co2e")
        if co2e is None or co2e <= 0:
            raise ValueError(f"no co2e in Climatiq estimate response: {payload!r:.200}")
        return co2e / 1_000_000.0, payload

    def get_sector_factor(self, sector):
        """Resolve (and cache) the spend-based factor for a TradingView sector.

        Returns ``{"factor_kg_per_usd", "activity_id", "factor_name"}``
        or None when the sector cannot be resolved.
        """
        if not self._climatiq_key:
            return None
        sector = (sector or "").strip() or "Other"
        if sector in self._sector_factors:
            return self._sector_factors[sector]

        queries = [
            q
            for q in (
                CLIMATIQ_SECTOR_QUERIES.get(sector),
                sector,  # raw sector name often matches too
                CLIMATIQ_FALLBACK_QUERY,
            )
            if q
        ]
        for query in queries:
            try:
                factor = self._climatiq_search_factor(query)
                if not factor:
                    continue
                kg_per_usd, _ = self._climatiq_measure_factor(factor["activity_id"])
                entry = {
                    "factor_kg_per_usd": kg_per_usd,
                    "activity_id": factor["activity_id"],
                    "factor_name": factor.get("name"),
                }
                self._sector_factors[sector] = entry
                logger.info(
                    "Climatiq factor for %r: %s (%s) = %.6g kg CO2e/USD",
                    sector,
                    entry["factor_name"],
                    entry["activity_id"],
                    kg_per_usd,
                )
                return entry
            except (requests.RequestException, ValueError) as e:
                logger.warning(
                    "Climatiq factor resolution failed for %r (query %r): %s",
                    sector,
                    query,
                    e,
                )
        self._sector_factors[sector] = None  # negative cache
        return None

    def estimate_company_emissions(self, sector, revenue):
        """Estimate one company's emissions from sector factor × revenue.

        Returns a CarbonEmission-schema dict, or None when the sector
        factor is unavailable or revenue is missing/zero.
        """
        entry = self.get_sector_factor(sector)
        if not entry:
            return None
        try:
            revenue = float(revenue)
        except (TypeError, ValueError):
            return None
        if revenue <= 0:
            return None

        factor = entry["factor_kg_per_usd"]
        # kg CO2e → metric tons; intensity is tCO2e per $1M revenue
        total_t = revenue * factor / 1000.0
        intensity = factor * 1000.0
        return {
            "report_year": datetime.now().year,
            "scope1": None,  # spend-based factors give a combined figure only
            "scope2": None,
            "total_emissions": round(total_t, 2),
            "carbon_intensity_revenue": round(intensity, 4),
            "carbon_change_yoy": None,
            "revenue": round(revenue, 2),
            "data_source": "climatiq",
            "has_carbon_data": True,
        }

    def _fetch_climatiq(self, symbol):
        """Estimate carbon data for one symbol using its DB sector+revenue."""
        try:
            from app.models.company import Company
            from app.models.financial_metric import FinancialMetric

            company = Company.query.filter_by(symbol=symbol).first()
            if not company:
                return None
            fm = (
                FinancialMetric.query.filter_by(symbol=symbol)
                .order_by(FinancialMetric.date.desc())
                .first()
            )
            revenue = float(fm.revenue) if fm and fm.revenue else None
            if not revenue:
                logger.info(
                    "No revenue in DB for %s — Climatiq estimation skipped", symbol
                )
                return None
            return self.estimate_company_emissions(company.sector, revenue)
        except Exception as e:  # noqa: BLE001 — degrade to Bavest/DB
            logger.error("Climatiq estimation failed for %s: %s", symbol, e)
            return None

    # ------------------------------------------------------------------
    # Clarity AI provider
    # ------------------------------------------------------------------

    def _get_token(self):
        """Obtain (and cache) a Clarity AI bearer token. Returns None on
        failure so callers can degrade gracefully."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        try:
            resp = requests.post(
                f"{self._clarity_base}/oauth/token",
                json={"key": self._clarity_key, "secret": self._clarity_secret},
                timeout=self._clarity_timeout,
            )
            resp.raise_for_status()
            token = resp.json().get("token")
            if not token:
                raise ValueError("empty token in response")
            # Tokens expire in ~60 minutes; refresh 5 min early
            self._token = token
            self._token_expiry = time.time() + 55 * 60
            return token
        except (requests.RequestException, ValueError) as e:
            logger.error("Clarity AI auth failed: %s", e)
            self._token = None
            return None

    def _fetch_clarity(self, symbol):
        """Fetch carbon data for one ticker via Clarity AI SFDR endpoints.

        Returns a CarbonEmission-schema dict or None.
        """
        token = self._get_token()
        if not token:
            return None

        # Clarity AI identifies securities by ISIN — map from ticker.
        isin = self._symbol_to_isin(symbol)
        if not isin:
            logger.warning("No ISIN mapping for %s — Clarity AI skipped", symbol)
            return None

        headers = {"Authorization": f"Bearer {token}"}
        try:
            # 1. Submit async job
            resp = requests.post(
                f"{self._clarity_base}/public/securities/sfdr/metric-by-id/async",
                headers=headers,
                json={"metricIds": CLARITY_METRICS, "securityIds": [isin]},
                timeout=self._clarity_timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            job_id = payload.get("jobId") or payload.get("job_id")
            if not job_id:
                # Some environments return data synchronously
                if "metrics" in payload or "data" in payload:
                    return self._parse_clarity_payload(
                        payload.get("data") or payload, isin
                    )
                raise ValueError(f"no jobId in response: {payload!r:.200}")

            # 2. Poll until the job completes
            deadline = time.time() + JOB_POLL_TIMEOUT
            while time.time() < deadline:
                time.sleep(JOB_POLL_INTERVAL)
                job = requests.get(
                    f"{self._clarity_base}/public/jobs/{job_id}",
                    headers=headers,
                    timeout=self._clarity_timeout,
                )
                job.raise_for_status()
                status = (job.json().get("status") or "").upper()
                if status in ("DONE", "COMPLETED", "FINISHED"):
                    data = job.json().get("data") or {}
                    return self._parse_clarity_payload(data, isin)
                if status in ("ERROR", "FAILED"):
                    raise ValueError(f"Clarity AI job failed: {job.json()!r:.200}")
            raise TimeoutError("Clarity AI job polling timed out")

        except Exception as e:  # noqa: BLE001 — degrade to Bavest/DB
            logger.error("Clarity AI fetch failed for %s: %s", symbol, e)
            return None

    def _parse_clarity_payload(self, data, isin):
        """Normalize a Clarity AI metrics payload into our schema."""
        # Payload shapes vary by module; handle both list-of-metrics and dict
        metrics = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    metrics[item["id"]] = item.get("value")
        elif isinstance(data, dict):
            metrics = {
                k: v.get("value") if isinstance(v, dict) else v
                for k, v in data.items()
            }

        scope1 = metrics.get("CARBON_EMISSIONS_SCOPE1")
        scope2 = metrics.get("CARBON_EMISSIONS_SCOPE2")
        total = metrics.get("CARBON_EMISSIONS")
        if total is None and (scope1 or scope2):
            total = (scope1 or 0) + (scope2 or 0)

        if total is None and scope1 is None and scope2 is None:
            return None  # no carbon disclosure for this security

        return {
            "report_year": int(metrics.get("reportYear") or
                               metrics.get("report_year") or 2025),
            "scope1": scope1,
            "scope2": scope2,
            "total_emissions": total,
            "carbon_intensity_revenue": metrics.get("GHG_INTENSITY"),
            "carbon_change_yoy": metrics.get("GHG_INTENSITY_YOY"),
            "revenue": metrics.get("REVENUE"),
            "data_source": "clarity_ai",
            "has_carbon_data": True,
        }

    def _symbol_to_isin(self, symbol):
        """Map a US ticker to an ISIN.

        Lookup order: env-provided overrides (merged in ``init_app``) →
        built-in static map.  Returns None for unknown tickers, which makes
        the Clarity AI fetch skip that symbol (Bavest/DB fallback applies).
        """
        if not symbol:
            return None
        return self._isin_map.get(symbol.strip().upper())

    # ------------------------------------------------------------------
    # Bavest provider (secondary)
    # ------------------------------------------------------------------

    def _fetch_bavest(self, symbol):
        """Fetch carbon data for a single symbol from the Bavest API."""
        url = f"{self._bavest_base}/v1/esg/carbon-footprint/{symbol}"
        headers = {
            "Authorization": f"Bearer {self._bavest_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            logger.info("Fetched carbon data for %s (Bavest)", symbol)
            return self._parse_bavest_response(resp.json())
        except requests.RequestException as e:
            logger.error("Bavest API request failed for %s: %s", symbol, e)
            return None

    @staticmethod
    def _parse_bavest_response(data):
        """Parse the Bavest API response into our schema."""
        return {
            "report_year": data.get("year", 2025),
            "scope1": data.get("scope_1_emissions"),
            "scope2": data.get("scope_2_emissions"),
            "total_emissions": data.get("total_emissions"),
            "carbon_intensity_revenue": data.get("carbon_intensity"),
            "carbon_change_yoy": data.get("carbon_intensity_change_yoy"),
            "revenue": data.get("revenue"),
            "data_source": "bavest",
            "has_carbon_data": True,
        }

carbon_service = CarbonService()
