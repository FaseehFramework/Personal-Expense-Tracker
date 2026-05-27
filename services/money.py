"""
Money utilities. All money is stored in the DB as integer fils (AED * 100).
Convert to/from decimals only at API and UI boundaries.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Union

from config import Config


Number = Union[int, float, str, Decimal]


def aed_to_fils(value: Number) -> int:
    """Convert any AED-denominated input to integer fils. Rounds half-up."""
    if value is None or value == "":
        raise ValueError("amount required")
    d = Decimal(str(value))
    fils = (d * Config.FILS_PER_AED).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(fils)


def fils_to_aed(fils: int) -> Decimal:
    """Convert fils back to a 2dp Decimal AED value."""
    return (Decimal(int(fils)) / Config.FILS_PER_AED).quantize(Decimal("0.01"))


def format_aed(fils: int) -> str:
    """Format fils as 'AED 1,234.56' (negative-aware)."""
    aed = fils_to_aed(fils)
    sign = "-" if aed < 0 else ""
    abs_aed = abs(aed)
    whole, frac = f"{abs_aed:.2f}".split(".")
    whole_with_commas = f"{int(whole):,}"
    return f"{sign}AED {whole_with_commas}.{frac}"
