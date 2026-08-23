"""CSV export utility — generates CSV downloads from screener results.

Implements POST /api/screener/export as described in the PRD section 5.4.
"""

import csv
import io
import logging

logger = logging.getLogger(__name__)

# Column order for CSV export
CSV_COLUMNS = [
    ("symbol", "Symbol"),
    ("name", "Company Name"),
    ("sector", "Sector"),
    ("close", "Close Price"),
    ("pe_ttm", "PE (TTM)"),
    ("pb", "PB"),
    ("dividend_yield", "Dividend Yield (%)"),
    ("turnover", "Turnover (%)"),
    ("market_cap", "Market Cap (USD)"),
    ("volume", "Volume"),
    ("week_52_change", "52-Week Change (%)"),
    ("net_profit_margin", "Net Profit Margin (%)"),
    ("revenue_growth", "Revenue Growth (%)"),
    ("carbon_intensity_revenue", "Carbon Intensity (tCO2e/$M)"),
    ("total_emissions", "Total Emissions (tCO2e)"),
    ("carbon_change_yoy", "Carbon Change YoY (%)"),
    ("carbon_report_year", "Carbon Report Year"),
    ("has_carbon_data", "Has Carbon Data"),
]


def generate_csv(results):
    """Generate CSV content from screener results.

    Args:
        results: list of dicts (same format as screener_service output)

    Returns:
        str: CSV file content
    """
    output = io.StringIO()
    writer = csv.writer(output)

    # Header row
    writer.writerow([label for _, label in CSV_COLUMNS])

    # Data rows
    for row in results:
        writer.writerow([
            _format_csv_value(row.get(key))
            for key, _ in CSV_COLUMNS
        ])

    return output.getvalue()


def _format_csv_value(val):
    """Format a value for CSV output."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, float):
        # Round to 2 decimal places
        return f"{val:.2f}"
    return str(val)
