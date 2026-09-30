"""The ACB report for one tax year (spec 04, Tax report): `/tax YEAR` and `ledger tax YEAR`.

Each disposition in the year with its proceeds, ACB, fees, gain or loss, the superficial loss
added to ACB, and whether that is still provisional; the year's CDR splits with the ACB per unit
before and after; and totals. Amounts are rounded to the cent here and only here, and the totals
add up the rounded lines. Not tax advice.
"""

import csv
import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from signalbench.live.acb import AcbBook

CENT = Decimal("0.01")
NOTES = (
    (
        "Not tax advice: a record-keeping aid that follows the CRA's published ACB method. "
        "Verify your return independently."
    ),
    (
        "Superficial losses count only the same CDR symbol as identical property. Confirm "
        "whether a US listing of the same company held elsewhere changes this."
    ),
)
CSV_HEADER = (
    "date", "symbol", "quantity", "proceeds", "acb", "fees", "gain_loss",
    "superficial_loss_added_to_acb", "allowed_gain_loss", "provisional",
)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class TaxLine:
    day: date
    symbol: str
    quantity: Decimal
    proceeds: Decimal
    acb: Decimal
    fees: Decimal
    gain: Decimal  # before the superficial-loss adjustment
    superficial: Decimal  # the part of a loss added to ACB
    allowed: Decimal  # gain + superficial
    provisional: bool  # a loss whose 30-day window has not passed


@dataclass(frozen=True)
class TaxSplit:
    day: date
    symbol: str
    ratio: Decimal
    per_unit_before: Decimal | None  # None when no units were held
    per_unit_after: Decimal | None


@dataclass(frozen=True)
class TaxTotals:
    proceeds: Decimal
    acb: Decimal
    fees: Decimal
    gain: Decimal
    superficial: Decimal
    allowed: Decimal


@dataclass(frozen=True)
class TaxReport:
    year: int
    lines: tuple[TaxLine, ...]
    splits: tuple[TaxSplit, ...]
    totals: TaxTotals  # sums of the rounded lines


def build_tax_report(books: Mapping[str, AcbBook], year: int) -> TaxReport:
    """The year's dispositions and CDR splits across every CDR, by date then symbol."""
    lines: list[TaxLine] = []
    splits: list[TaxSplit] = []
    for symbol, book in books.items():
        for d in book.dispositions:
            if d.trade.day.year != year:
                continue
            lines.append(
                TaxLine(
                    day=d.trade.day, symbol=symbol, quantity=d.trade.quantity,
                    proceeds=cents(d.proceeds), acb=cents(d.acb), fees=cents(d.trade.fee),
                    gain=cents(d.gain), superficial=cents(d.denied), allowed=cents(d.allowed),
                    provisional=d.provisional,
                )
            )
        for record in book.splits:
            if record.split.day.year != year:
                continue
            before, after = record.per_unit_before, record.per_unit_after
            splits.append(
                TaxSplit(
                    day=record.split.day, symbol=symbol, ratio=record.split.ratio,
                    per_unit_before=None if before is None else cents(before),
                    per_unit_after=None if after is None else cents(after),
                )
            )
    zero = Decimal(0)
    totals = TaxTotals(
        proceeds=sum((line.proceeds for line in lines), zero),
        acb=sum((line.acb for line in lines), zero),
        fees=sum((line.fees for line in lines), zero),
        gain=sum((line.gain for line in lines), zero),
        superficial=sum((line.superficial for line in lines), zero),
        allowed=sum((line.allowed for line in lines), zero),
    )
    return TaxReport(
        year=year,
        lines=tuple(sorted(lines, key=lambda line: (line.day, line.symbol))),
        splits=tuple(sorted(splits, key=lambda split: (split.day, split.symbol))),
        totals=totals,
    )


def tax_csv(report: TaxReport) -> str:
    """One row per disposition, then a totals row."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_HEADER)
    for line in report.lines:
        writer.writerow([
            line.day.isoformat(), line.symbol, f"{line.quantity.normalize():f}", line.proceeds,
            line.acb, line.fees, line.gain, line.superficial, line.allowed,
            "yes" if line.provisional else "no",
        ])
    t = report.totals
    writer.writerow(
        ["total", "", "", t.proceeds, t.acb, t.fees, t.gain, t.superficial, t.allowed, ""]
    )
    return buffer.getvalue()


def _per_unit(value: Decimal | None) -> str:
    return "-" if value is None else f"C${value}"


def tax_text(report: TaxReport) -> list[str]:
    """The report as lines for the terminal and the /tax summary."""
    lines = [f"ACB report {report.year}: {len(report.lines)} dispositions"]
    for line in report.lines:
        flag = " (provisional)" if line.provisional else ""
        superficial = ""
        if line.superficial:
            superficial = f" | superficial loss added to ACB C${line.superficial}"
        lines.append(
            f"{line.day} {line.symbol} sold {line.quantity.normalize():f} | proceeds "
            f"C${line.proceeds} | ACB C${line.acb} | fees C${line.fees} | gain/loss "
            f"C${line.gain}{superficial} | allowed C${line.allowed}{flag}"
        )
    t = report.totals
    lines.append(
        f"totals: proceeds C${t.proceeds} | ACB C${t.acb} | fees C${t.fees} | gain/loss "
        f"C${t.gain} | superficial C${t.superficial} | allowed C${t.allowed}"
    )
    for split in report.splits:
        lines.append(
            f"split {split.day} {split.symbol} {split.ratio.normalize():f}-for-1 | ACB per unit "
            f"{_per_unit(split.per_unit_before)} -> {_per_unit(split.per_unit_after)}"
        )
    return lines + list(NOTES)
