"""Decimal/quantity formatting helpers.

The API contract represents money and storage volumes as JSON strings to avoid
floating-point ambiguity. These helpers produce stable, deterministic strings.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_Q4 = Decimal("0.0001")


def to_decimal(value: object) -> Decimal:
    """Coerce a value to Decimal without introducing binary-float error."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    # str() first so floats do not leak their binary representation.
    return Decimal(str(value))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_Q4, rounding=ROUND_HALF_UP)


def fmt_money(value: object) -> str:
    """Format money with at least two decimal places (e.g. `12.50`, `10000.00`)."""
    s = format(_quantize(to_decimal(value)), "f").rstrip("0")
    if s.endswith("."):
        return s + "00"
    int_part, _, frac = s.partition(".")
    if len(frac) < 2:
        return f"{int_part}.{frac.ljust(2, '0')}"
    return s


def fmt_volume(value: object) -> str:
    """Format a storage volume with at least one decimal place (e.g. `800.0`)."""
    s = format(_quantize(to_decimal(value)), "f").rstrip("0")
    if s.endswith("."):
        return s + "0"
    return s


def fmt_money_optional(value: object | None) -> str | None:
    return None if value is None else fmt_money(value)


def fmt_volume_optional(value: object | None) -> str | None:
    return None if value is None else fmt_volume(value)
