"""How an amount of money reads in a message a person sees: dollars and cents.

Spend is added up to the ten-thousandth of a dollar, and budgets are stored to four places. A reason shown
on a page said "$31.3457854 of its $30.0000 budget"; a person reads "$31.35 of its $30.00 budget".
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

CENT = Decimal("0.01")


def usd(amount: Any) -> str:
    """The amount as dollars and cents, with its sign: "$31.35"."""
    return f"${Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)}"
