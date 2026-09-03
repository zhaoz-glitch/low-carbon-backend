"""Generate carbon trend chart PNGs for export.

Uses matplotlib to recreate the 5-year carbon trend chart shown in the
frontend drawer.  Charts are saved as PNG files into a caller-provided
directory so they can be bundled into the export ZIP.
"""

import logging
from pathlib import Path

from app.utils.mock_data import get_carbon_trend

logger = logging.getLogger(__name__)

_plt = None


def _get_plt():
    global _plt
    if _plt is None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        _plt = plt
    return _plt


def generate_carbon_trend_chart(symbol: str, output_dir: Path) -> Path | None:
    """Generate a PNG chart for the given symbol and save into output_dir.

    Returns the Path of the saved file, or None on failure.
    """
    try:
        trend = get_carbon_trend(symbol)
    except Exception:
        logger.exception("Failed to fetch carbon trend for %s", symbol)
        return None

    if not trend:
        logger.warning("No carbon trend data for %s", symbol)
        return None

    try:
        plt = _get_plt()
        fig, ax1 = plt.subplots(figsize=(8, 4.5), dpi=120)

        years = [t["report_year"] for t in trend]
        emissions = [
            float(t["total_emissions"]) if t.get("total_emissions") is not None else 0
            for t in trend
        ]
        # CarbonEmission.to_dict() exposes the intensity under
        # carbon_intensity_revenue; accept carbon_intensity as an alias.
        intensity = [
            float(t.get("carbon_intensity_revenue") or t.get("carbon_intensity") or 0)
            for t in trend
        ]

        # Bar chart for total emissions
        color_bar = "#34d399"
        ax1.bar(years, emissions, color=color_bar, alpha=0.7, width=0.6, label="Total emissions (tCO2e)")
        ax1.set_xlabel("Year", fontsize=10)
        ax1.set_ylabel("Total emissions (tCO2e)", fontsize=10, color=color_bar)
        ax1.tick_params(axis="y", labelcolor=color_bar)
        ax1.set_xticks(years)

        # Line chart for intensity on secondary axis
        ax2 = ax1.twinx()
        color_line = "#3b82f6"
        ax2.plot(years, intensity, color=color_line, marker="o", linewidth=2, markersize=5, label="Intensity (t/$M)")
        ax2.set_ylabel("Intensity (t/$M revenue)", fontsize=10, color=color_line)
        ax2.tick_params(axis="y", labelcolor=color_line)

        fig.suptitle(f"{symbol} — 5-Year Carbon Trend", fontsize=13, fontweight="bold", y=0.98)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=8)

        fig.tight_layout(rect=[0, 0, 1, 0.95])

        out_path = output_dir / f"{symbol}_carbon_trend.png"
        fig.savefig(out_path, format="png", bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception:
        logger.exception("Chart generation failed for %s", symbol)
        return None
