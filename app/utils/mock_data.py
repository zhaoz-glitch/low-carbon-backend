"""Mock data seeder — populates the database with sample S&P 500 companies.

In production, this data would come from the TradingView and Bavest APIs via
the ETL pipeline.  For MVP development without API keys, we seed realistic
sample data so the full screening pipeline can be tested end-to-end.

Covers ~20 well-known US stocks across multiple sectors.
"""

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)


# --- Sample company data ---
# Format: (symbol, name, sector, industry, exchange, market_cap)
SAMPLE_COMPANIES = [
    ("AAPL", "Apple Inc.", "Technology", "Consumer Electronics", "NASDAQ", 3310000000000),
    ("MSFT", "Microsoft Corporation", "Technology", "Software", "NASDAQ", 3150000000000),
    ("GOOGL", "Alphabet Inc.", "Communication Services", "Internet Content", "NASDAQ", 2100000000000),
    ("AMZN", "Amazon.com Inc.", "Consumer Cyclical", "Internet Retail", "NASDAQ", 1900000000000),
    ("NVDA", "NVIDIA Corporation", "Technology", "Semiconductors", "NASDAQ", 2950000000000),
    ("META", "Meta Platforms Inc.", "Communication Services", "Internet Content", "NASDAQ", 1340000000000),
    ("TSLA", "Tesla Inc.", "Consumer Cyclical", "Auto Manufacturers", "NASDAQ", 820000000000),
    ("JPM", "JPMorgan Chase & Co.", "Financial Services", "Banks - Diversified", "NYSE", 580000000000),
    ("V", "Visa Inc.", "Financial Services", "Credit Services", "NYSE", 520000000000),
    ("JNJ", "Johnson & Johnson", "Healthcare", "Drug Manufacturers", "NYSE", 390000000000),
    ("WMT", "Walmart Inc.", "Consumer Defensive", "Discount Stores", "NYSE", 420000000000),
    ("XOM", "Exxon Mobil Corporation", "Energy", "Oil & Gas Integrated", "NYSE", 480000000000),
    ("PG", "Procter & Gamble Co.", "Consumer Defensive", "Household Products", "NYSE", 390000000000),
    ("KO", "Coca-Cola Co.", "Consumer Defensive", "Beverages", "NYSE", 280000000000),
    ("HD", "Home Depot Inc.", "Consumer Cyclical", "Home Improvement Retail", "NYSE", 380000000000),
    ("AVGO", "Broadcom Inc.", "Technology", "Semiconductors", "NASDAQ", 780000000000),
    ("MA", "Mastercard Inc.", "Financial Services", "Credit Services", "NYSE", 450000000000),
    ("UNH", "UnitedHealth Group Inc.", "Healthcare", "Healthcare Plans", "NYSE", 530000000000),
    ("NEE", "NextEra Energy Inc.", "Utilities", "Utilities - Renewable", "NYSE", 165000000000),
    ("CVX", "Chevron Corporation", "Energy", "Oil & Gas Integrated", "NYSE", 290000000000),
]


# --- Sample financial metrics (as of latest date) ---
# Format: (pe_ttm, pb, dividend_yield, turnover, volume,
#          week_52_change, net_profit_margin, revenue_growth, close, market_cap)
SAMPLE_FINANCIALS = {
    "AAPL": (28.3, 45.2, 0.52, 0.8, 55000000, 15.0, 25.3, 8.0, 215.50, 3310000000000),
    "MSFT": (35.1, 12.8, 0.72, 0.9, 22000000, 22.0, 36.1, 14.0, 425.00, 3150000000000),
    "GOOGL": (24.5, 6.1, 0.00, 1.2, 25000000, 30.0, 24.0, 12.0, 175.00, 2100000000000),
    "AMZN": (45.2, 8.5, 0.00, 2.5, 45000000, 18.0, 6.3, 11.0, 185.00, 1900000000000),
    "NVDA": (65.3, 52.0, 0.03, 3.5, 350000000, 120.0, 50.0, 95.0, 120.00, 2950000000000),
    "META": (27.8, 8.2, 0.42, 1.8, 18000000, 45.0, 35.0, 16.0, 500.00, 1340000000000),
    "TSLA": (72.5, 11.3, 0.00, 6.5, 95000000, -5.0, 10.0, 8.0, 255.00, 820000000000),
    "JPM": (11.2, 1.9, 2.35, 1.0, 8000000, 25.0, 30.0, 5.0, 200.00, 580000000000),
    "V": (30.5, 11.2, 0.78, 0.7, 5500000, 10.0, 50.0, 9.0, 275.00, 520000000000),
    "JNJ": (15.8, 5.1, 3.10, 0.6, 7000000, -2.0, 18.0, 3.0, 155.00, 390000000000),
    "WMT": (22.4, 6.8, 1.25, 0.8, 15000000, 12.0, 2.5, 6.0, 72.00, 420000000000),
    "XOM": (9.5, 2.1, 3.25, 1.2, 18000000, 8.0, 10.0, -2.0, 115.00, 480000000000),
    "PG": (24.1, 6.5, 2.40, 0.5, 6000000, 5.0, 18.0, 2.0, 155.00, 390000000000),
    "KO": (22.8, 9.2, 3.05, 0.7, 12000000, -3.0, 23.0, 1.0, 65.00, 280000000000),
    "HD": (21.5, 35.2, 2.45, 1.0, 3500000, 10.0, 10.0, 2.0, 380.00, 380000000000),
    "AVGO": (30.2, 8.5, 1.30, 1.5, 20000000, 35.0, 30.0, 20.0, 1750.00, 780000000000),
    "MA": (36.1, 50.5, 0.55, 0.5, 3000000, 8.0, 45.0, 10.0, 480.00, 450000000000),
    "UNH": (19.2, 5.1, 1.45, 0.8, 2500000, 5.0, 6.0, 14.0, 560.00, 530000000000),
    "NEE": (21.5, 4.2, 2.80, 0.5, 8000000, 15.0, 20.0, 7.0, 80.00, 165000000000),
    "CVX": (10.8, 1.8, 4.15, 1.0, 8000000, -5.0, 12.0, -3.0, 150.00, 290000000000),
}


# --- Sample carbon emissions (latest report year) ---
# Format: (symbol, report_year, scope1, scope2, carbon_intensity, yoy_change, revenue)
SAMPLE_CARBON = {
    "AAPL": (2024, 150000, 4000000, 14.5, -12.3, 383000000000),
    "MSFT": (2024, 2800000, 3700000, 18.2, -15.5, 245000000000),
    "GOOGL": (2024, 50000, 5000000, 24.1, -8.7, 307000000000),
    "AMZN": (2024, 6000000, 9500000, 32.5, -5.2, 575000000000),
    "NVDA": (2024, 80000, 350000, 7.8, -22.1, 61000000000),
    "META": (2024, 150000, 3200000, 28.4, -38.0, 135000000000),
    "TSLA": (2024, 2000000, 2800000, 65.3, -15.0, 96000000000),
    "JPM": (2024, 120000, 850000, 5.1, -3.2, 158000000000),
    "V": (2024, 8000, 120000, 1.2, -10.5, 36000000000),
    "JNJ": (2024, 350000, 1200000, 15.8, -4.1, 89000000000),
    "WMT": (2024, 1200000, 8000000, 22.4, -6.8, 648000000000),
    "XOM": (2024, 120000000, 25000000, 300.5, 8.2, 344000000000),
    "PG": (2024, 450000, 800000, 3.2, -7.5, 84000000000),
    "KO": (2024, 250000, 500000, 2.8, -5.1, 43000000000),
    "HD": (2024, 680000, 2400000, 8.5, -3.2, 157000000000),
    "AVGO": (2024, 95000, 480000, 9.5, -18.0, 36000000000),
    "MA": (2024, 6000, 95000, 0.8, -12.0, 25000000000),
    "UNH": (2024, 850000, 1500000, 12.1, -5.8, 371000000000),
    "NEE": (2024, 50000, 1200000, 8.5, -25.0, 25000000000),
    "CVX": (2024, 55000000, 8000000, 215.3, 5.1, 200000000000),
}


def _build_carbon_history_rows(symbol):
    """Build 5 annual CarbonEmission rows (latest_year-4 .. latest_year).

    The latest year uses the values from SAMPLE_CARBON; earlier years are
    back-computed from the same formula previously used for the in-memory
    trend dict, so the chart output is unchanged — but every year now lives
    in the ``carbon_emissions`` table instead of Python memory.
    """
    latest_year, s1, s2, latest_ci, latest_yoy, revenue = SAMPLE_CARBON[symbol]
    scope_ratio = s1 / (s1 + s2) if (s1 + s2) else 0.5

    rows = []
    intensities = []
    for year_offset in range(4, -1, -1):
        year = latest_year - year_offset
        if year_offset == 0:
            ci = latest_ci
        else:
            ci = round(latest_ci * (1 + abs(latest_yoy / 100) * year_offset * 0.8), 2)
        intensities.append((year, ci))

    for i, (year, ci) in enumerate(intensities):
        total = round(ci * revenue / 1000000, 2)
        if i == 0:
            yoy = None
        else:
            prev_ci = intensities[i - 1][1]
            yoy = round((ci - prev_ci) / prev_ci * 100, 2) if prev_ci else None
        rows.append({
            "symbol": symbol,
            "report_year": year,
            "scope1": round(total * scope_ratio, 2),
            "scope2": round(total * (1 - scope_ratio), 2),
            "total_emissions": total,
            "carbon_intensity_revenue": ci,
            "carbon_change_yoy": yoy,
            "revenue": revenue,
            "data_source": "mock",
            "has_carbon_data": True,
        })
    return rows


def seed_mock_data(db):
    """Seed the database with mock companies, financials, and carbon data.

    Called during app initialization. Only seeds if tables are empty.
    """
    from app.models.company import Company
    from app.models.financial_metric import FinancialMetric
    from app.models.carbon_emission import CarbonEmission
    from app.models.preset_template import PresetTemplate

    if Company.query.first() is not None:
        logger.info("Database already seeded — skipping mock data insertion")
        return

    logger.info("Seeding database with mock data...")

    today = date(2026, 7, 23)  # simulating a recent market date

    for sym, name, sector, industry, exchange, mcap in SAMPLE_COMPANIES:
        company = Company(
            symbol=sym,
            name=name,
            sector=sector,
            industry=industry,
            exchange=exchange,
            market_cap=mcap,
        )
        db.session.add(company)

        # Financial metrics
        fin = SAMPLE_FINANCIALS[sym]
        fm = FinancialMetric(
            symbol=sym,
            date=today,
            pe_ttm=fin[0],
            pb=fin[1],
            dividend_yield=fin[2],
            turnover=fin[3],
            volume=fin[4],
            week_52_change=fin[5],
            net_profit_margin=fin[6],
            revenue_growth=fin[7],
            close=fin[8],
            market_cap=fin[9],
        )
        db.session.add(fm)

        # Carbon emissions — 5 years of history per company
        for row in _build_carbon_history_rows(sym):
            db.session.add(CarbonEmission(**row))

    # Seed preset templates from the PRD
    templates = [
        PresetTemplate(
            name="低碳价值陷阱",
            description="PE < 15 且碳强度同比下降 > 5%",
            use_case="寻找被低估的转型中公司",
            filters={
                "price_earnings_ttm": {"max": 15},
                "carbon_change_yoy": {"max": -5},
                "has_carbon_data": "true",
            },
        ),
        PresetTemplate(
            name="绿色高成长",
            description="营收增长 > 20% 且碳强度 < 行业均值 50%",
            use_case="挖掘绿色赛道成长股",
            filters={
                "revenue_growth": {"min": 20},
                "carbon_intensity_revenue": {"max": 15},
                "has_carbon_data": "true",
            },
        ),
        PresetTemplate(
            name="净零先锋",
            description="碳强度同比下降 > 15% 且绝对排放量 < 500万吨",
            use_case="关注激进减排公司",
            filters={
                "carbon_change_yoy": {"max": -15},
                "total_emissions": {"max": 5000000},
                "has_carbon_data": "true",
            },
        ),
        PresetTemplate(
            name="高股息绿色标的",
            description="股息率 > 3% 且碳强度 < 200 tCO2e/$M",
            use_case="稳健收益型绿色投资",
            filters={
                "dividend_yield_recent": {"min": 3},
                "carbon_intensity_revenue": {"max": 200},
                "has_carbon_data": "true",
            },
        ),
    ]
    for tpl in templates:
        db.session.add(tpl)

    db.session.commit()
    logger.info("Mock data seeded: %d companies, %d templates",
                len(SAMPLE_COMPANIES), len(templates))


def get_carbon_trend(symbol):
    """Return 5-year carbon trend data for a given symbol.

    Used by GET /api/stock/{symbol}/carbon-trend endpoint.  All data now
    comes from the ``carbon_emissions`` table via an ORM query — no more
    in-memory dicts.
    """
    from app.models.carbon_emission import CarbonEmission

    records = (
        CarbonEmission.query
        .filter_by(symbol=symbol)
        .order_by(CarbonEmission.report_year.asc())
        .all()
    )
    return [r.to_dict() for r in records]
