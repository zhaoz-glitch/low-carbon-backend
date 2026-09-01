"""Screener universe: tickers, exchanges, TradingView symbols, and ISINs.

Clarity AI SFDR securities endpoints identify holdings by ISIN.
TradingView scanner requests use ``EXCHANGE:SYMBOL`` tickers.
"""

# (symbol, name, sector, industry, exchange, market_cap)
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

COMPANY_ISINS = {
    "AAPL": "US0378331005",
    "MSFT": "US5949181045",
    "GOOGL": "US02079K3059",
    "AMZN": "US0231351067",
    "NVDA": "US67066G1040",
    "META": "US30303M1027",
    "TSLA": "US88160R1014",
    "JPM": "US46625H1005",
    "V": "US92826C8394",
    "JNJ": "US4781601046",
    "WMT": "US9311421039",
    "XOM": "US30231G1022",
    "PG": "US7427181091",
    "KO": "US1912161007",
    "HD": "US4370761029",
    "AVGO": "US11135F1012",
    "MA": "US57636Q1040",
    "UNH": "US91324P1021",
    "NEE": "US65339F1012",
    "CVX": "US1667641005",
}


def tv_ticker(symbol: str, exchange: str | None = None) -> str:
    """Return TradingView scanner ticker, e.g. NASDAQ:AAPL."""
    if ":" in symbol:
        return symbol
    if exchange:
        return f"{exchange}:{symbol}"
    for row in SAMPLE_COMPANIES:
        if row[0] == symbol:
            return f"{row[4]}:{symbol}"
    return f"NASDAQ:{symbol}"


def universe_symbols() -> list[str]:
    return [row[0] for row in SAMPLE_COMPANIES]


def isin_to_symbol() -> dict[str, str]:
    return {isin: symbol for symbol, isin in COMPANY_ISINS.items()}
