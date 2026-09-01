"""Fetch the full US market universe from TradingView's scanner API.

Usage:
    venv/Scripts/python scripts/fetch_all_us_stocks.py [--type stock|etf|all]

Dumps one row per symbol to exports/us_market_snapshot.csv with price,
volume, market cap, valuation multiples and classification columns.
The scanner serves near-real-time quotes during US trading hours;
on weekends/holidays it returns the most recent close.
"""

import argparse
import os
import sys
import time

import pandas as pd

# Add project root to sys.path so `app` imports work when run standalone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradingview_screener import Query, col  # noqa: E402

COLUMNS = [
    "name",            # ticker symbol
    "description",     # company / fund name
    "type",            # stock / etf / fund / dr ...
    "exchange",
    "sector",
    "industry",
    "close",           # last price
    "change",          # 1-day % change
    "change_1_week",
    "change_1_month",
    "change_1_year",
    "volume",
    "market_cap_basic",
    "price_earnings_ttm",
    "price_book_fq",
    "dividends_yield",
    "turnover",
    "net_margin",
]

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports"
)


def fetch_universe(type_filter: str = "all") -> pd.DataFrame:
    """Fetch every symbol on the US ('america') market scanner."""
    q = (
        Query()
        .select(*COLUMNS)
        .set_markets("america")
        .where()
        .limit(100_000)
    )
    if type_filter != "all":
        q = q.where(col("type") == type_filter)

    t0 = time.time()
    total, df = q.get_scanner_data()
    print(f"fetched {total} symbols in {time.time() - t0:.1f}s")
    return df


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--type", default="all", choices=["all", "stock", "etf", "fund"],
        help="restrict to a symbol type (default: all)",
    )
    args = parser.parse_args()

    df = fetch_universe(args.type)

    # Sort: stocks first, then by market cap desc (NaN caps last)
    df = df.sort_values(
        ["market_cap_basic"], ascending=False, na_position="last"
    ).reset_index(drop=True)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    suffix = "" if args.type == "all" else f"_{args.type}s"
    ts = time.strftime("%Y%m%d_%H%M")
    out = os.path.join(OUTPUT_DIR, f"us_market_snapshot{suffix}_{ts}.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")

    # Summary
    print(f"saved -> {out}")
    print(f"rows: {len(df)}")
    print("\nbreakdown by type:")
    print(df["type"].value_counts(dropna=False).to_string())
    print("\nbreakdown by exchange (top 10):")
    print(df["exchange"].value_counts(dropna=False).head(10).to_string())
    print("\nlargest 5 by market cap:")
    cols = ["name", "description", "close", "market_cap_basic", "sector"]
    print(df[cols].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
