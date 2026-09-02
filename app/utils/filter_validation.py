"""Validate and normalize filter payloads for the screener API.

The frontend sends filter values as strings.  This module turns those strings
into numbers, rejects garbage like "abc", and unifies percentage inputs
("10%" -> 10) with decimal inputs ("0.1" -> 0.1) so both forms work for fields
whose database values are stored as percentages.
"""

import math
from typing import Any


# Fields whose database values are stored as percentages.
PERCENT_FIELDS = {
    "turnover",
    "dividend_yield_recent",
    "change_1_year",
    "net_margin",
    "carbon_change_yoy",
}


def normalize_number(value: Any, unit: str | None = None) -> float:
    """Convert a user-supplied filter value into a normalized float.

    Rules:
      - Strip whitespace and a trailing percent sign.
      - For percentage fields, "10%" and "10" both mean 10 (the DB stores
        values as percentages already).
      - Reject empty values, non-numeric strings, NaN and infinities.

    Raises:
        ValueError: with a human-readable message if the input is invalid.
    """
    if value is None or value == "":
        raise ValueError("value is required")

    raw = str(value).strip()
    if not raw:
        raise ValueError("value is required")

    # Strip a single trailing "%" (with optional spaces) for percentage fields
    # OR when the user typed one explicitly regardless of declared unit.
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    elif unit == "%":
        # Accept bare numbers as percentages too, e.g. "0.1" means 0.1%.
        pass

    try:
        num = float(raw)
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a valid number") from exc

    if math.isnan(num):
        raise ValueError("'nan' is not a valid number")
    if math.isinf(num):
        raise ValueError("infinity is not allowed")

    return num


def validate_filter_value(key: str, condition: Any, field_meta: dict) -> Any:
    """Validate a single filter condition and return a normalized value.

    Accepts:
      - dict with numeric/boundable "min" and/or "max" keys
      - a plain number

    Returns:
        The condition with all present numeric boundaries normalized to floats.
    """
    unit = field_meta.get("unit")

    if isinstance(condition, dict):
        out = {}
        for boundary in ("min", "max"):
            if boundary not in condition:
                continue
            try:
                out[boundary] = normalize_number(condition[boundary], unit)
            except ValueError as exc:
                raise ValueError(f"Filter '{key}' {boundary}: {exc}") from exc
        return out

    # Plain numeric value used as a threshold (mapped to a boundary upstream).
    try:
        return normalize_number(condition, unit)
    except ValueError as exc:
        raise ValueError(f"Filter '{key}': {exc}") from exc


def validate_filters(filters: dict) -> dict:
    """Validate and normalize an entire filters dict from the API request.

    Builds a small field metadata map from the live service definitions so that
    percentage-aware normalization is applied consistently.

    Raises:
        ValueError: aggregated list of problems found in the payload.
    """
    from app.services.tradingview_service import tradingview_service
    from app.services.carbon_service import carbon_service

    meta = {}
    for field in tradingview_service.get_market_fields_metadata():
        meta[field["key"]] = field
    for field in carbon_service.get_carbon_fields_metadata():
        meta[field["key"]] = field

    normalized = {}
    errors = []

    for key, condition in filters.items():
        if key == "has_carbon_data":
            if condition not in {"true", "false", "all"}:
                errors.append(f"'{key}' must be 'true', 'false' or 'all'")
            else:
                normalized[key] = condition
            continue

        field = meta.get(key)
        if field is None:
            # Unknown keys are silently ignored (keeps the API tolerant), but
            # we still validate recognized ones strictly.
            normalized[key] = condition
            continue

        try:
            normalized[key] = validate_filter_value(key, condition, field)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        raise ValueError("; ".join(errors))

    return normalized
