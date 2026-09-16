"""Decimal helpers for NUMERIC(18,4) money columns. Floats never touch an amount."""

from decimal import ROUND_HALF_UP, Decimal

SCALE = Decimal("0.0001")
MAX_ABS = Decimal("99999999999999.9999")  # largest value NUMERIC(18,4) can hold
ZERO = Decimal("0.0000")


def quantize(value: Decimal) -> Decimal:
    """Round to 4 decimal places and reject values the database column cannot store."""
    if not value.is_finite():
        raise ValueError("amount must be a finite number")
    result = value.quantize(SCALE, rounding=ROUND_HALF_UP)
    if abs(result) > MAX_ABS:
        raise ValueError("amount exceeds the supported range of NUMERIC(18,4)")
    return result + ZERO  # turns -0.0000 into 0.0000


def format_amount(value: Decimal) -> str:
    """Render with at least 2 and at most 4 decimals: 1260 -> '1260.00', 0.0125 -> '0.0125'."""
    whole, _, fraction = f"{quantize(value):f}".partition(".")
    fraction = fraction.rstrip("0").ljust(2, "0")
    return f"{whole}.{fraction}"
