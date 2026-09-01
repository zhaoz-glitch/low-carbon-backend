"""Core screener service — the data aggregation layer.

Implements the ETL pipeline described in the PRD:
  1. Query financial metrics matching user's financial filter conditions
  2. INNER/LEFT JOIN with carbon emission data
  3. Apply carbon-related filters
  4. Pagination + sorting
  5. Return unified result set
"""

import logging
from datetime import datetime
from sqlalchemy import or_, and_, func, distinct
from sqlalchemy.orm import aliased

from app.extensions import db
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.models.carbon_emission import CarbonEmission

logger = logging.getLogger(__name__)


class ScreenerService:
    """Core filtering logic for the Low-Carbon Value Screener."""

    # Map API filter keys to database columns
    FINANCIAL_FILTER_MAP = {
        "market_cap_basic": FinancialMetric.market_cap,
        "turnover": FinancialMetric.turnover,
        "price_earnings_ttm": FinancialMetric.pe_ttm,
        "price_book_value": FinancialMetric.pb,
        "dividend_yield_recent": FinancialMetric.dividend_yield,
        "volume": FinancialMetric.volume,
        "change_1_year": FinancialMetric.week_52_change,
        "net_margin": FinancialMetric.net_profit_margin,
        "revenue_growth": FinancialMetric.revenue_growth,
    }

    CARBON_FILTER_MAP = {
        "carbon_intensity_revenue": CarbonEmission.carbon_intensity_revenue,
        "total_emissions": CarbonEmission.total_emissions,
        "carbon_change_yoy": CarbonEmission.carbon_change_yoy,
    }

    SORT_FIELD_MAP = {
        "market_cap_basic": FinancialMetric.market_cap,
        "close": FinancialMetric.close,
        "price_earnings_ttm": FinancialMetric.pe_ttm,
        "turnover": FinancialMetric.turnover,
        "dividend_yield_recent": FinancialMetric.dividend_yield,
        "carbon_intensity_revenue": CarbonEmission.carbon_intensity_revenue,
        "carbon_change_yoy": CarbonEmission.carbon_change_yoy,
        "total_emissions": CarbonEmission.total_emissions,
    }

    def run_screener(self, filters, page=1, page_size=50,
                     sort_by="market_cap_basic", sort_order="desc"):
        """Execute the screening pipeline.

        Args:
            filters: dict of filter conditions (see PRD section 4.2)
            page: page number (1-indexed)
            page_size: results per page
            sort_by: field to sort by
            sort_order: "asc" or "desc"

        Returns:
            dict with total, page, pageSize, data list
        """
        logger.info("Running screener with filters=%s, page=%s", filters, page)

        # Separate financial filters from carbon filters
        financial_filters = {}
        carbon_filters = {}
        include_no_carbon_data = False
        has_carbon_filter = filters.get("has_carbon_data", "all")

        for key, value in filters.items():
            if key == "include_no_carbon_data":
                include_no_carbon_data = bool(value)
                continue
            if key == "has_carbon_data":
                has_carbon_filter = value
                continue
            if key in self.FINANCIAL_FILTER_MAP:
                financial_filters[key] = value
            elif key in self.CARBON_FILTER_MAP:
                carbon_filters[key] = value

        # --- Build base query ---
        # Company + latest FinancialMetric (via correlated subquery)
        query = (
            db.session.query(
                Company.symbol,
                Company.name,
                Company.sector,
                FinancialMetric.date.label("market_date"),
                FinancialMetric.close,
                FinancialMetric.pe_ttm,
                FinancialMetric.turnover,
                FinancialMetric.market_cap,
                FinancialMetric.pb,
                FinancialMetric.dividend_yield,
                FinancialMetric.volume,
                FinancialMetric.week_52_change,
                FinancialMetric.net_profit_margin,
                FinancialMetric.revenue_growth,
                CarbonEmission.carbon_intensity_revenue,
                CarbonEmission.total_emissions,
                CarbonEmission.carbon_change_yoy,
                CarbonEmission.report_year.label("carbon_report_year"),
                CarbonEmission.scope1,
                CarbonEmission.scope2,
            )
            .join(FinancialMetric, Company.symbol == FinancialMetric.symbol)
        )

        # --- Filter: only latest financial record per symbol (correlated subquery) ---
        # Use aliased model to avoid self-correlation issues in SQLAlchemy.
        FM = aliased(FinancialMetric)
        latest_fin_date = (
            db.session.query(func.max(FM.date))
            .filter(FM.symbol == Company.symbol)
            .scalar_subquery()
        )
        query = query.filter(FinancialMetric.date == latest_fin_date)

        # --- JOIN carbon data ---
        use_inner = has_carbon_filter == "true" and not include_no_carbon_data
        if use_inner:
            query = query.join(
                CarbonEmission, Company.symbol == CarbonEmission.symbol
            )
            # Filter to latest carbon record per symbol
            CE = aliased(CarbonEmission)
            latest_carbon_year = (
                db.session.query(func.max(CE.report_year))
                .filter(CE.symbol == Company.symbol)
                .scalar_subquery()
            )
            query = query.filter(CarbonEmission.report_year == latest_carbon_year)
        else:
            query = query.outerjoin(
                CarbonEmission, Company.symbol == CarbonEmission.symbol
            )
            # For LEFT JOIN: filter to latest carbon record OR NULL
            CE = aliased(CarbonEmission)
            latest_carbon_year = (
                db.session.query(func.max(CE.report_year))
                .filter(CE.symbol == Company.symbol)
                .scalar_subquery()
            )
            query = query.filter(
                or_(
                    CarbonEmission.report_year.is_(None),
                    CarbonEmission.report_year == latest_carbon_year,
                )
            )

        # --- Apply financial filters ---
        for key, condition in financial_filters.items():
            col = self.FINANCIAL_FILTER_MAP[key]
            if isinstance(condition, dict):
                if "min" in condition:
                    query = query.filter(col >= condition["min"])
                if "max" in condition:
                    query = query.filter(col <= condition["max"])
            elif isinstance(condition, (int, float)):
                query = query.filter(col >= condition)

        # --- Apply carbon filters ---
        for key, condition in carbon_filters.items():
            col = self.CARBON_FILTER_MAP[key]
            if isinstance(condition, dict):
                if "min" in condition:
                    query = query.filter(col >= condition["min"])
                if "max" in condition:
                    query = query.filter(col <= condition["max"])
            elif isinstance(condition, (int, float)):
                query = query.filter(col >= condition)

        # --- Handle "no carbon data" filter ---
        if has_carbon_filter == "false":
            query = query.filter(CarbonEmission.carbon_intensity_revenue.is_(None))
        elif has_carbon_filter == "true":
            query = query.filter(CarbonEmission.carbon_intensity_revenue.isnot(None))

        # --- Count total (before pagination) ---
        # Use a subquery to count distinct symbols
        count_subq = query.with_entities(Company.symbol).distinct().subquery()
        total = db.session.query(func.count()).select_from(count_subq).scalar() or 0

        # --- Sorting ---
        sort_col = self.SORT_FIELD_MAP.get(sort_by, FinancialMetric.market_cap)
        if sort_order == "asc":
            query = query.order_by(sort_col.asc())
        else:
            query = query.order_by(sort_col.desc())

        # --- Pagination ---
        offset = (page - 1) * page_size
        query = query.offset(offset).limit(page_size)

        # --- Execute ---
        results = query.all()

        # --- Format results ---
        data = [self._format_result_row(row) for row in results]

        logger.info("Screener returned %s results (page %s of %s total)",
                     len(data), page, total)

        return {
            "total": total,
            "page": page,
            "pageSize": page_size,
            "data": data,
        }

    def _format_result_row(self, row):
        """Convert a SQLAlchemy result row to a dict."""
        def safe_float(val):
            return float(val) if val is not None else None

        return {
            "symbol": row.symbol,
            "name": row.name,
            "sector": row.sector,
            "market_date": row.market_date.isoformat() if row.market_date else None,
            "close": safe_float(row.close),
            "pe_ttm": safe_float(row.pe_ttm),
            "turnover": safe_float(row.turnover),
            "market_cap": safe_float(row.market_cap),
            "pb": safe_float(row.pb),
            "dividend_yield": safe_float(row.dividend_yield),
            "volume": safe_float(row.volume),
            "week_52_change": safe_float(row.week_52_change),
            "net_profit_margin": safe_float(row.net_profit_margin),
            "revenue_growth": safe_float(row.revenue_growth),
            "carbon_intensity_revenue": safe_float(row.carbon_intensity_revenue),
            "total_emissions": safe_float(row.total_emissions),
            "carbon_change_yoy": safe_float(row.carbon_change_yoy),
            "carbon_report_year": row.carbon_report_year,
            "scope1": safe_float(row.scope1),
            "scope2": safe_float(row.scope2),
            "has_carbon_data": row.carbon_intensity_revenue is not None,
        }


# Singleton
screener_service = ScreenerService()
