"""Money in a message a person reads is dollars and cents."""

from decimal import Decimal

from meta_harness.money import usd


def test_an_amount_reads_as_dollars_and_cents():
    assert usd("31.3457854") == "$31.35"
    assert usd(Decimal("30.0000")) == "$30.00"
    assert usd("0.8271") == "$0.83"
    assert usd(10) == "$10.00"
    assert usd("0.005") == "$0.01", "half a cent rounds up"
