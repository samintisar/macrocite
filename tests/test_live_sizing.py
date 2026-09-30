"""Spec 05, Live CDR sizing: the whole-unit rule's three branches, the caps, and the 2.5% risk
bound on rounding up. Every number is exact Decimal arithmetic, checked by hand."""

from decimal import Decimal

from signalbench.live.sizing import CdrSize, size_cdr

RISK = Decimal("0.02")


def _size(
    us_close: str, us_stop: str, cdr_close: str, equity: str = "100", cash: str | None = None
) -> CdrSize | None:
    return size_cdr(
        us_signal_close=Decimal(us_close), us_stop=Decimal(us_stop),
        cdr_close=Decimal(cdr_close), equity=Decimal(equity),
        uncommitted_cash=Decimal(equity if cash is None else cash), risk_pct=RISK,
        max_positions=3,
    )


def test_floor_whole_units_with_a_limit_one_percent_over() -> None:
    # stop 4%: cdr_stop 9.70, 0.40 a unit; 2.00 / 0.40 = 5 units, capped at 33.33 / 10.10 = 3.30
    size = _size("101", "97", "10.10")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("floor", "limit", Decimal(3))
    assert (size.cdr_stop, size.limit) == (Decimal("9.7000"), Decimal("10.20"))  # 10.201 down
    assert (size.risk, size.cost) == (Decimal("1.2000"), Decimal("30.30"))


def test_the_spec_example_at_c120_is_one_unit() -> None:
    size = _size("200", "188", "38.40", equity="120")  # 1.04 units: floor 1 is >= 75% of it
    assert size is not None
    assert (size.rule, size.units, size.limit) == ("floor", Decimal(1), Decimal("38.78"))
    assert (size.cdr_stop, size.stop_pct, size.risk) == (
        Decimal("36.0960"), Decimal("0.06000000"), Decimal("2.3040")
    )


def test_ceil_when_the_floor_is_under_75_percent_and_the_risk_stays_within_2_5_percent() -> None:
    # stop 8%: 1.20 a unit, 2.00 / 1.20 = 1.67 units; floor 1 < 1.25, so ceil 2: risk 2.40
    size = _size("100", "92", "15")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("ceil", "limit", Decimal(2))
    assert (size.risk, size.cost, size.limit) == (Decimal("2.4000"), Decimal(30), Decimal("15.15"))


def test_fractional_when_rounding_up_would_risk_more_than_2_5_percent() -> None:
    # stop 10%: 3.00 a unit, 0.67 units; 1 unit would risk 3.00 > 2.50
    size = _size("100", "90", "30")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("fractional", "market", Decimal("0.666666"))
    assert size.limit == Decimal("30.30")


def test_fractional_when_one_unit_is_worth_more_than_a_third_of_equity() -> None:
    size = _size("200", "188", "38.40")  # the spec example at C$100: 1 unit is C$38.40 > 33.33
    assert size is not None
    assert (size.rule, size.units) == ("fractional", Decimal("0.868055"))  # 33.33 / 38.40
    assert size.cost < Decimal(100) / 3


def test_uncommitted_cash_caps_the_size_and_none_left_means_no_order() -> None:
    size = _size("101", "97", "10.10", cash="20")  # 20 / 10.10 = 1.98; 2 units cost 20.20
    assert size is not None
    assert (size.rule, size.units) == ("fractional", Decimal("1.980198"))
    assert _size("101", "97", "10.10", cash="0") is None
    assert _size("101", "97", "10.10", cash="-5") is None


def test_whole_units_must_fit_the_cash_cap_at_the_limit_price() -> None:
    # Cash C$30.50: 3 units cost C$30.30 at the close but C$30.60 at the C$10.20 limit, so they
    # do not fit; 2 units are under 75% of the 3.02 target, and 4 do not fit either.
    size = _size("101", "97", "10.10", cash="30.50")
    assert size is not None
    assert (size.rule, size.order_type, size.units) == ("fractional", "market", Decimal("3.019801"))
    assert size.cost <= Decimal("30.50")
    # Cash C$50.70 (equity 200): 5 units cost C$51.00 at the limit, so 4 units (80% of the
    # 5.02 target) are placed, and 4 x C$10.20 is what the order holds.
    size = _size("101", "97", "10.10", equity="200", cash="50.70")
    assert size is not None
    assert (size.rule, size.units, size.committed) == ("floor", Decimal(4), Decimal("40.80"))
    assert size.units * size.limit <= Decimal("50.70")


def test_ceil_must_fit_the_cap_at_the_limit_price_too() -> None:
    # stop 8%, close 15, limit 15.15: ceil 2 costs 30.30 at the limit, over a cap of 30.20
    size = _size("100", "92", "15", cash="30.20")
    assert size is not None
    assert (size.rule, size.units) == ("fractional", Decimal("1.666666"))
