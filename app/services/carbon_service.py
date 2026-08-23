"""Carbon emission data service.

In production this would call the Bavest API (or Intrinio) to fetch annual
carbon emissions data.  Data is updated 1-2 times per year per company
(annual report cycle).

For MVP, data is pre-seeded in the local database by ``mock_data.py``.
"""

import logging
import requests

logger = logging.getLogger(__name__)


class CarbonService:
    """Fetch and manage carbon emission data."""

    def __init__(self, app=None):
        self.app = app
        self._api_key = ""
        self._base_url = ""

    def init_app(self, app):
        self.app = app
        self._api_key = app.config.get("BAVEST_API_KEY", "")
        self._base_url = app.config.get("BAVEST_BASE_URL", "https://api.bavest.co")

    def fetch_carbon_data(self, symbol):
        """Fetch carbon emission data for a single symbol from Bavest API.

        Args:
            symbol: ticker symbol, e.g. "AAPL"

        Returns:
            dict with carbon fields, or None if fetch fails.
        """
        if not self._api_key:
            logger.warning("Bavest API key not configured — skipping live fetch")
            return None

        url = f"{self._base_url}/v1/esg/carbon-footprint/{symbol}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            logger.info("Fetched carbon data for %s", symbol)
            return self._parse_bavest_response(data)
        except requests.RequestException as e:
            logger.error("Bavest API request failed for %s: %s", symbol, e)
            return None

    def fetch_and_store(self, symbol, db):
        """Fetch carbon data from Bavest and store in the database.

        Returns True on success, False on failure.
        """
        from app.models.carbon_emission import CarbonEmission
        from app.models.company import Company
        from datetime import datetime

        raw = self.fetch_carbon_data(symbol)
        if not raw:
            return False

        # Upsert carbon emission record
        existing = (
            CarbonEmission.query.filter_by(
                symbol=symbol, report_year=raw.get("report_year")
            ).first()
        )

        if existing:
            # Update existing record
            for key, val in raw.items():
                if hasattr(existing, key):
                    setattr(existing, key, val)
        else:
            record = CarbonEmission(symbol=symbol, **raw)
            db.session.add(record)

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
                "source": "Bavest",
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
                "source": "Bavest",
                "update_frequency": "annual",
                "description": "Scope 1 + Scope 2 总排放量（吨 CO2 当量）",
            },
            {
                "key": "carbon_change_yoy",
                "label": "碳排同比变化 (Carbon Change YoY)",
                "type": "threshold",
                "unit": "%",
                "ops": ["<", ">", "<=", ">="],
                "source": "Bavest",
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
