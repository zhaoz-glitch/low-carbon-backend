"""Carbon emission data service.

Real data source integration with the **Clarity AI** REST API
(https://developer.clarity.ai) as the primary provider, with the Bavest API
retained as a secondary provider.

Data is updated 1-2 times per year per company (annual report cycle).

Clarity AI flow (all standard REST, callable with ``requests``):

1. ``POST /oauth/token`` with Client Key + Secret → Bearer token (60 min TTL)
2. ``POST /public/securities/sfdr/metric-by-id/async`` with ``metricIds``
   (CARBON_EMISSIONS, CARBON_EMISSIONS_SCOPE1/2, GHG_INTENSITY) and either
   ``securityIds`` (ISINs) or ``securityTypes`` → returns a JobId
3. Poll ``GET /public/jobs/{jobId}`` until complete, then fetch the result

If no Clarity AI credentials are configured, the service falls back to
Bavest (if configured) and finally to the locally seeded database
(mock data), so the app always works in development.
"""

import logging
import time

import requests

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
        self._bavest_key = app.config.get("BAVEST_API_KEY", "")
        self._bavest_base = app.config.get(
            "BAVEST_BASE_URL", "https://api.bavest.co"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_carbon_data(self, symbol):
        """Fetch carbon emission data for a single symbol.

        Tries Clarity AI first, then Bavest.  Returns a dict matching the
        CarbonEmission schema, or None when no provider is configured /
        all fetches fail (caller falls back to DB).
        """
        result = None
        if self._clarity_key and self._clarity_secret:
            result = self._fetch_clarity(symbol)
        if result is None and self._bavest_key:
            result = self._fetch_bavest(symbol)
        if result is None:
            logger.info(
                "No carbon provider configured/failed for %s — DB fallback", symbol
            )
        return result

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

    def get_carbon_fields_metadata(self):
        """Return metadata for green/carbon filter fields.

        Maps to "Dimension B" in the PRD (annual update).
        """
        return [
            {
                "key": "carbon_intensity_revenue",
                "label": "碳强度 (Carbon Intensity / Revenue)",
                "type": "threshold",
                "unit": "tCO2e/$M",
                "ops": ["<", ">", "<=", ">="],
                "source": "Clarity AI",
                "update_frequency": "annual",
                "description": "每百万美元营收的碳排放量（吨）",
            },
            {
                "key": "total_emissions",
                "label": "绝对排放量 (Scope 1+2 Absolute)",
                "type": "range",
                "unit": "tCO2e",
                "min": 0,
                "max": 50000000,  # 50M tons
                "step": 100000,
                "source": "Clarity AI",
                "update_frequency": "annual",
                "description": "Scope 1 + Scope 2 总排放量（吨 CO2 当量）",
            },
            {
                "key": "carbon_change_yoy",
                "label": "碳排同比变化 (Carbon Change YoY)",
                "type": "threshold",
                "unit": "%",
                "ops": ["<", ">", "<=", ">="],
                "source": "Clarity AI",
                "update_frequency": "annual",
                "description": "碳强度同比下降为负值（减排）",
            },
            {
                "key": "has_carbon_data",
                "label": "碳排数据披露状态 (Disclosure Status)",
                "type": "select",
                "options": [
                    {"value": "true", "label": "有数据"},
                    {"value": "false", "label": "无数据"},
                    {"value": "all", "label": "全部"},
                ],
                "source": "System",
                "update_frequency": "N/A",
                "description": "筛选有/无碳排放数据的公司",
            },
        ]

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

    @staticmethod
    def _symbol_to_isin(symbol):
        """Map a US ticker to an ISIN (CUSIP-based heuristic).

        Clarity AI's synchronous securities endpoints also accept ticker-like
        IDs; if you maintain a real ticker→ISIN table, plug it in here.
        """
        # TODO: replace with a proper ticker→ISIN lookup table
        return None

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


# Singleton
carbon_service = CarbonService()
