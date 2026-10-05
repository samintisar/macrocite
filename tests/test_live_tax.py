"""Spec 04: the tax-year ACB report, its CSV, and its notes."""

from datetime import date
from decimal import Decimal

from sqlmodel import Session

from live_helpers import add_pair, load_example, make_ledger, record_example
from signalbench.live.tax import NOTES, tax_csv, tax_text


def test_the_rebuy_example_s_report_with_the_superficial_loss(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    record_example(ledger, load_example("acb_rebuy"))
    report = ledger.tax_report(2026)
    assert tax_csv(report) == (
        "date,symbol,quantity,proceeds,acb,fees,gain_loss,superficial_loss_added_to_acb,"
        "allowed_gain_loss,provisional\n"
        "2026-02-02,ZTST,4,140.00,124.80,0.00,15.20,0.00,15.20,no\n"
        "2026-02-16,ZTST,2,58.00,62.20,0.00,-4.20,2.10,-2.10,no\n"
        "total,,,198.00,187.00,0.00,11.00,2.10,13.10,\n"
    )
    assert tax_text(report)[2] == (
        "2026-02-16 ZTST sold 2 | proceeds C$58.00 | ACB C$62.20 | fees C$0.00 | gain/loss "
        "C$-4.20 | superficial loss added to ACB C$2.10 | allowed C$-2.10"
    )
    assert tax_text(report)[-2:] == list(NOTES)
    assert ledger.tax_report(2025).lines == ()


def test_a_loss_inside_its_30_days_is_provisional(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session, today=date(2026, 3, 10))
    record_example(ledger, load_example("acb_rebuy"))
    [gain, loss] = ledger.tax_report(2026).lines
    assert (gain.provisional, loss.provisional) == (False, True)
    assert tax_text(ledger.tax_report(2026))[2].endswith("allowed C$-2.10 (provisional)")


def test_the_year_s_cdr_splits_show_the_acb_per_unit_before_and_after(session: Session) -> None:
    add_pair(session, "TST", "ZTST")
    ledger = make_ledger(session)
    ledger.record_cash(Decimal("100.00"), date(2026, 3, 2), "deposit")
    ledger.record_fill(cdr_symbol="ZTST", side="buy", quantity=Decimal(3),
                       price_cad=Decimal("30.00"), trade_date=date(2026, 3, 2))
    ledger.record_split(kind="cdr_split", symbol="ZTST", ex_date=date(2026, 3, 9),
                        ratio=Decimal(2), source="owner")
    report = ledger.tax_report(2026)
    [split] = report.splits
    assert (split.ratio, split.per_unit_before, split.per_unit_after) == (
        Decimal(2), Decimal("30.00"), Decimal("15.00")
    )
    assert "split 2026-03-09 ZTST 2-for-1 | ACB per unit C$30.00 -> C$15.00" in tax_text(report)
    assert report.lines == ()
