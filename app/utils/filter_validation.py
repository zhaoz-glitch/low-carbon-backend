"""Validate and normalize filter payloads for the screener API.

The frontend sends filter values as strings.  This module turns those strings
into numbers, rejects garbage like "abc", and unifies percentage inputs.

Percentage semantics (fields whose unit is "%"):
  - User input is a FRACTION: "10%" and "0.1" both mean ten percent.
  - The database stores percentage points (6.36 == 6.36%), so fraction
    inputs are multiplied by 100 before comparison ("10%" -> 0.1 -> 10).
  - Preset templates store percentage points; they are divided by 100 when
    served so the API speaks fractions in both directions.
"""

import math
from typing import Any


# Fields whose database values are stored as percentage points.
PERCENT_FIELDS = {
    "turnover",
    "dividend_yield_recent",
    "dividends_yield",
    "change_1_year",
    "net_margin",
    "carbon_change_yoy",
}


def _is_percent_field(key: str, unit: str | None) -> bool:
    return unit == "%" or key in PERCENT_FIELDS


def normalize_number(value: Any, unit: str | None = None) -> float:
    """Convert a user-supplied filter value into a normalized float.

    Rules:
      - Strip whitespace; strip a trailing "%" and divide by 100, so "10%"
        becomes the fraction 0.1.
      - Plain numbers are kept as-is (for percent fields they are fractions,
        e.g. "0.1" means ten percent).
      - Reject empty values, non-numeric strings, NaN and infinities.

    Raises:
        ValueError: with a human-readable message if the input is invalid.
    """
    if value is None or value == "":
        raise ValueError("value is required")

    raw = str(value).strip()
    if not raw:
        raise ValueError("value is required")

    is_percent_input = False
    if raw.endswith("%"):
        raw = raw[:-1].strip()
        is_percent_input = True
        if not raw:
            raise ValueError(f"'{value}' is not a valid number")

    try:
        num = float(raw)
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a valid number") from exc

    if math.isnan(num):
        raise ValueError("'nan' is not a valid number")
    if math.isinf(num):
        raise ValueError("infinity is not allowed")

    if is_percent_input:
        num = num / 100.0

    return num


def _to_db_value(key: str, num: float, unit: str | None) -> float:
    """Convert a user-facing fraction into the DB's percentage points."""
    if _is_percent_field(key, unit):
        return num * 100.0
    return num


def validate_filter_value(key: str, condition: Any, field_meta: dict) -> Any:
    """Validate a single filter condition and return a normalized value.

    Accepts:
      - dict with numeric/boundable "min" and/or "max" keys
      - a plain number

    Returns:
        The condition with boundaries normalized to floats and, for percent
        fields, scaled from fraction to percentage points for DB comparison.
    """
    unit = field_meta.get("unit")

    if isinstance(condition, dict):
        out = {}
        for boundary in ("min", "max"):
            if boundary not in condition:
                continue
            try:
                num = normalize_number(condition[boundary], unit)
            except ValueError as exc:
                raise ValueError(f"Filter '{key}' {boundary}: {exc}") from exc
            out[boundary] = _to_db_value(key, num, unit)
        return out

    # Plain numeric value used as a threshold (mapped to a boundary upstream).
    try:
        num = normalize_number(condition, unit)
    except ValueError as exc:
        raise ValueError(f"Filter '{key}': {exc}") from exc
    return _to_db_value(key, num, unit)


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


def template_filters_to_api(filters: Any) -> Any:
    """Convert stored template filters into the API's fraction convention.

    Templates store percentage points (legacy convention); the API expects
    fractions for percent fields, so divide those values by 100 when serving.
    """
    if not isinstance(filters, dict):
        return filters

    out = {}
    for key, condition in filters.items():
        if not _is_percent_field(key, None):
            out[key] = condition
            continue

        if isinstance(condition, dict):
            conv = {}
            for boundary in ("min", "max"):
                val = condition.get(boundary)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    conv[boundary] = val / 100.0
                else:
                    conv[boundary] = val
            out[key] = conv
        elif isinstance(condition, (int, float)) and not isinstance(condition, bool):
            out[key] = condition / 100.0
        else:
            out[key] = condition
    return out
