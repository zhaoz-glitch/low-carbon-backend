"""Carbon emission data service — Clarity AI SFDR REST (official API only).

Flow (see https://developer.clarity.ai/docs/sfdr):
  1. POST /oauth/token  with client key + secret  → Bearer (~60 min)
  2. POST /public/securities/sfdr/metric-by-id/async  with ISINs + metricIds
  3. Poll GET /public/job/{jobId}/status
  4. GET  /public/job/{jobId}/download  → CSV/JSON

Without credentials the screener keeps the seeded mock carbon rows.
We do not scrape Clarity (or any other) HTML pages.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import datetime, timezone

import requests
from flask import current_app

from app.universe import COMPANY_ISINS, isin_to_symbol, universe_symbols

logger = logging.getLogger(__name__)

SFDR_METRICS = [
    "CARBON_EMISSIONS_SCOPE1",
    "CARBON_EMISSIONS_SCOPE2",
    "CARBON_EMISSIONS",
    "GHG_INTENSITY",
]


def _num(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


class CarbonService:
    """Fetch annual Scope 1+2 / intensity from Clarity AI."""

    def __init__(self, app=None):
        self.app = app
        self._api_key = ""
        self._api_secret = ""
        self._base_url = "https://api.clarity.ai/clarity/v1"
        self._token = None
        self._token_expires = 0.0

    def init_app(self, app):
        self.app = app
        self._api_key = app.config.get("CLARITY_API_KEY", "") or ""
        self._api_secret = app.config.get("CLARITY_API_SECRET", "") or ""
        self._base_url = app.config.get(
            "CLARITY_BASE_URL", "https://api.clarity.ai/clarity/v1"
        ).rstrip("/")
        # Legacy Bavest key is ignored for carbon; keep attr so old env still loads.
        self._bavest_key = app.config.get("BAVEST_API_KEY", "")

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _get_token(self) -> str | None:
        if not self.is_configured():
            logger.warning("Clarity AI key/secret not configured — skipping live carbon fetch")
            return None
        now = time.time()
        if self._token and now < self._token_expires:
            return self._token

        url = f"{self._base_url}/oauth/token"
        try:
            resp = requests.post(
                url,
                json={"key": self._api_key, "secret": self._api_secret},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
        except requests.RequestException as exc:
            logger.error("Clarity OAuth failed: %s", exc)
            return None

        token = payload.get("token") or payload.get("access_token")
        if not token:
            logger.error("Clarity OAuth response missing token: %s", list(payload.keys()))
            return None
        # Tokens last ~60 minutes; refresh a bit early.
        self._token = token
        self._token_expires = now + 50 * 60
        return token

    def fetch_universe_carbon(self, symbols=None) -> list[dict] | None:
        """Pull SFDR carbon metrics for the screener universe.

        Returns a list of carbon row dicts, or None if not configured / failed.
        """
        token = self._get_token()
        if not token:
            return None

        wanted = [s.upper() for s in (symbols or universe_symbols())]
        security_ids = [COMPANY_ISINS[s] for s in wanted if s in COMPANY_ISINS]
        if not security_ids:
            logger.warning("No ISINs mapped for requested symbols")
            return None

        job_id = self._start_sfdr_job(token, security_ids)
        if not job_id:
            return None
        if not self._wait_for_job(token, job_id):
            return None
        raw_rows = self._download_job(token, job_id)
        if raw_rows is None:
            return None
        return self._rows_to_carbon(raw_rows, wanted)

    def _start_sfdr_job(self, token: str, security_ids: list[str]) -> str | None:
        url = f"{self._base_url}/public/securities/sfdr/metric-by-id/async"
        body = {"metricIds": SFDR_METRICS, "securityIds": security_ids}
        try:
            resp = requests.post(url, json=body, headers=self._headers(token), timeout=30)
            resp.raise_for_status()
            payload = resp.json() or {}
        except requests.RequestException as exc:
            logger.error("Clarity SFDR job create failed: %s", exc)
            return None

        job_id = (
            payload.get("jobId")
            or payload.get("job_id")
            or payload.get("uuid")
            or payload.get("id")
        )
        if not job_id:
            logger.error("Clarity SFDR job response missing jobId: %s", payload)
            return None
        logger.info("Clarity SFDR job started: %s", job_id)
        return str(job_id)

    def _wait_for_job(self, token: str, job_id: str) -> bool:
        url = f"{self._base_url}/public/job/{job_id}/status"
        timeout = 180
        if self.app is not None:
            timeout = int(self.app.config.get("CLARITY_JOB_TIMEOUT", 180))
        else:
            try:
                timeout = int(current_app.config.get("CLARITY_JOB_TIMEOUT", 180))
            except RuntimeError:
                timeout = 180
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(url, headers=self._headers(token), timeout=20)
                resp.raise_for_status()
                payload = resp.json() or {}
            except requests.RequestException as exc:
                logger.error("Clarity job status failed: %s", exc)
                return False

            status = str(
                payload.get("status") or payload.get("state") or payload.get("jobStatus") or ""
            ).upper()
            if status in ("SUCCESS", "SUCCEEDED", "DONE", "COMPLETED", "COMPLETE"):
                return True
            if status in ("FAILED", "ERROR", "CANCELLED", "CANCELED"):
                logger.error("Clarity job %s failed: %s", job_id, payload)
                return False
            time.sleep(3)
        logger.error("Clarity job %s timed out after %ss", job_id, timeout)
        return False

    def _download_job(self, token: str, job_id: str) -> list[dict] | None:
        url = f"{self._base_url}/public/job/{job_id}/download"
        try:
            resp = requests.get(url, headers=self._headers(token), timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Clarity job download failed: %s", exc)
            return None

        ctype = (resp.headers.get("Content-Type") or "").lower()
        text = resp.text or ""
        if "json" in ctype or text.lstrip().startswith(("[", "{")):
            try:
                payload = resp.json()
            except ValueError:
                payload = None
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict):
                for key in ("data", "results", "metrics", "items"):
                    if isinstance(payload.get(key), list):
                        return payload[key]
                return [payload]
        return self._parse_csv(text)

    @staticmethod
    def _parse_csv(text: str) -> list[dict]:
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]

    def _rows_to_carbon(self, raw_rows: list[dict], symbols: list[str]) -> list[dict]:
        """Pivot long (isin, metric, value) rows into our carbon_emissions schema."""
        lookup = isin_to_symbol()
        wanted = set(symbols)
        by_symbol: dict[str, dict] = {}

        for row in raw_rows:
            isin = (
                row.get("securityId")
                or row.get("security_id")
                or row.get("isin")
                or row.get("ISIN")
            )
            metric = (
                row.get("metricId")
                or row.get("metric_id")
                or row.get("metric")
                or row.get("id")
            )
            value = _num(row.get("value") or row.get("metricValue") or row.get("val"))
            year = row.get("year") or row.get("reportYear") or row.get("report_year")
            symbol = lookup.get(str(isin).replace(" ", "").upper()) if isin else None
            if not symbol and row.get("symbol"):
                symbol = str(row["symbol"]).upper()
            if not symbol or symbol not in wanted:
                continue
            bucket = by_symbol.setdefault(
                symbol,
                {"symbol": symbol, "metrics": {}, "report_year": None},
            )
            if metric:
                bucket["metrics"][str(metric).upper()] = value
            if year:
                try:
                    bucket["report_year"] = int(str(year)[:4])
                except (TypeError, ValueError):
                    pass

        current_year = datetime.now(timezone.utc).year
        results = []
        for symbol, bucket in by_symbol.items():
            metrics = bucket["metrics"]
            scope1 = metrics.get("CARBON_EMISSIONS_SCOPE1")
            scope2 = metrics.get("CARBON_EMISSIONS_SCOPE2")
            total = metrics.get("CARBON_EMISSIONS")
            if total is None and (scope1 is not None or scope2 is not None):
                total = (scope1 or 0) + (scope2 or 0)
            intensity = metrics.get("GHG_INTENSITY")
            results.append(
                {
                    "symbol": symbol,
                    "report_year": bucket["report_year"] or (current_year - 1),
                    "scope1": scope1,
                    "scope2": scope2,
                    "total_emissions": total,
                    "carbon_intensity_revenue": intensity,
                    "revenue": None,
                    "data_source": "clarity",
                    "has_carbon_data": total is not None or intensity is not None,
                }
            )
        logger.info("Parsed Clarity carbon rows for %s symbols", len(results))
        return results

    def fetch_carbon_data(self, symbol):
        """Single-symbol helper used by older callers."""
        rows = self.fetch_universe_carbon([symbol])
        if not rows:
            return None
        return rows[0]

    def fetch_and_store(self, symbol, db):
        """Fetch one symbol and upsert. Prefer ``sync_carbon`` for the universe."""
        from app.models.carbon_emission import CarbonEmission

        raw = self.fetch_carbon_data(symbol)
        if not raw:
            return False
        existing = CarbonEmission.query.filter_by(
            symbol=symbol, report_year=raw.get("report_year")
        ).first()
        if existing:
            for key, val in raw.items():
                if hasattr(existing, key):
                    setattr(existing, key, val)
        else:
            db.session.add(CarbonEmission(**raw))
        db.session.commit()
        return True

    def get_carbon_fields_metadata(self):
        """Return metadata for green/carbon filter fields (Dimension B)."""
        return [
            {
                "key": "carbon_intensity_revenue",
                "label": "Carbon Intensity",
                "type": "threshold",
                "unit": "tCO2e/$M",
                "ops": ["<", ">", "<=", ">="],
                "source": "Clarity AI",
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
                "source": "Clarity AI",
                "update_frequency": "annual",
                "description": "Scope 1 + Scope 2 total emissions (tCO2e)",
            },
            {
                "key": "carbon_change_yoy",
                "label": "Carbon Change YoY",
                "type": "threshold",
                "unit": "%",
                "ops": ["<", ">", "<=", ">="],
                "source": "Clarity AI",
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


carbon_service = CarbonService()
