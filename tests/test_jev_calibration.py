from datetime import UTC, date, datetime
from decimal import Decimal

from pytest import approx
from sqlmodel import Session

from signalbench.db.models import (
    DocType,
    DocumentTicker,
    JevReading,
    Price,
    RawDocument,
    Ticker,
    TickerKind,
)
from signalbench.jev.calibration import (
    Sample,
    calibrate,
    calibration_sections,
    decile,
    label_for,
    render_calibration_report,
)
from signalbench.jev.store import calibration_samples
from signalbench.market.legal_close import LegalCloses
from strategy_helpers import WeekdaySessions, weekdays

BEFORE = date(2026, 9, 1)
AFTER = date(2026, 9, 16)

# Hand-worked example (p_negative, p_neutral, p_positive, label):
#   p_positive deciles: [0.05] flat | [0.15 up, 0.12 flat] | [0.85 up] | [0.95 up, 0.91 down]
#   ECE(p_positive vs up) = (0.05 + 2 x 0.365 + 0.15 + 2 x 0.43) / 6 = 1.79 / 6
#   p_negative deciles: [0.05, 0.08, 0.05, 0.02, 0.04] one down | [0.15] none
#   ECE(p_negative vs down) = (5 x |0.048 - 0.2| + 0.15) / 6 = 0.91 / 6
#   argmax: neutral, neutral, neutral, positive, positive, positive -> 4 of 6 right
#   labels: up 3, flat 2, down 1 -> majority baseline "up" at 3 / 6
EXAMPLE = [
    Sample(BEFORE, 0.15, 0.80, 0.05, "flat"),
    Sample(BEFORE, 0.05, 0.80, 0.15, "up"),
    Sample(BEFORE, 0.08, 0.80, 0.12, "flat"),
    Sample(BEFORE, 0.05, 0.10, 0.85, "up"),
    Sample(BEFORE, 0.02, 0.03, 0.95, "up"),
    Sample(AFTER, 0.04, 0.05, 0.91, "down"),
]


def test_deciles_include_the_upper_edge_in_the_top_bucket() -> None:
    assert [decile(p) for p in (0.0, 0.0999, 0.1, 0.3, 0.7, 0.95, 1.0)] == [0, 0, 1, 3, 7, 9, 9]


def test_reliability_ece_and_accuracy_match_the_hand_computed_example() -> None:
    section = calibrate("All", EXAMPLE)
    assert section.samples == 6
    by_bucket = {bucket.index: bucket for bucket in section.positive if bucket.count}
    assert sorted(by_bucket) == [0, 1, 8, 9]
    assert by_bucket[1].count == 2
    assert by_bucket[1].mean_predicted == approx(0.135)
    assert by_bucket[1].observed_rate == approx(0.5)
    assert by_bucket[9].mean_predicted == approx(0.93)
    assert section.positive_ece == approx(1.79 / 6)
    negative = {bucket.index: bucket for bucket in section.negative if bucket.count}
    assert negative[0].count == 5 and negative[0].mean_predicted == approx(0.048)
    assert negative[0].observed_rate == approx(0.2)
    assert section.negative_ece == approx(0.91 / 6)
    assert section.accuracy == approx(4 / 6)
    assert (section.majority_label, section.majority_rate) == ("up", approx(0.5))
    assert section.label_counts == {"down": 1, "flat": 2, "up": 3}


def test_sections_split_at_jevs_release() -> None:
    sections = calibration_sections(EXAMPLE)
    assert [(s.name, s.samples) for s in sections] == [
        ("All documents", 6),
        ("Legal close before 2026-09-15", 5),
        ("Legal close on or after 2026-09-15", 1),
    ]


def test_an_empty_section_has_no_ece_or_accuracy() -> None:
    empty = calibrate("Nothing", [])
    assert (empty.samples, empty.positive_ece, empty.accuracy, empty.majority_label) == (
        0, None, None, None,
    )


def test_labels_are_the_5_session_excess_return_over_qqq() -> None:
    sessions = weekdays(date(2024, 1, 1), 10)
    qqq = {day: 100.0 for day in sessions} | {sessions[6]: 101.0}
    stock = {day: 100.0 for day in sessions}
    t = sessions[1]
    assert label_for(t, stock | {sessions[6]: 104.0}, qqq, sessions) == "up"  # 4% - 1% = 3%
    assert label_for(t, stock | {sessions[6]: 103.0}, qqq, sessions) == "flat"  # exactly +2%
    assert label_for(t, stock | {sessions[6]: 98.5}, qqq, sessions) == "down"  # -2.5%
    assert label_for(sessions[5], stock, qqq, sessions) is None  # 5 sessions later is unknown
    missing = {day: price for day, price in stock.items() if day != sessions[6]}
    assert label_for(t, missing, qqq, sessions) is None


def test_the_report_has_every_table_and_the_split() -> None:
    text = render_calibration_report(
        calibration_sections(EXAMPLE),
        readings=7,
        unlabeled=1,
        builds={"typesafe/jev-1.13-20260917": 7},
        written_on=date(2026, 10, 1),
    )
    assert text.startswith("# Jev calibration report (spec 03, information only)\n")
    assert "| 0.1-0.2 | 2 | 0.135 | 0.500 |" in text
    assert f"ECE: {1.79 / 6:.3f}" in text
    assert "Argmax impact accuracy: 66.7% (majority baseline `up`: 50.0%)" in text
    assert "## Legal close on or after 2026-09-15 (1 documents)" in text
    assert "Nothing is adjusted automatically" in text


def _price(session: Session, ticker: Ticker, day: date, close: float) -> None:
    value = Decimal(str(close))
    session.add(
        Price(ticker_id=ticker.id, date=day, open=value, high=value, low=value, close=value,
              adj_close=value, volume=1_000)
    )


def test_samples_are_labeled_from_stored_prices(session: Session) -> None:
    sessions = weekdays(date(2024, 1, 1), 12)
    aaa = Ticker(symbol="AAA", company_name="Aaa Corp", kind=TickerKind.us_stock)
    qqq = Ticker(symbol="QQQ", company_name="QQQ", kind=TickerKind.benchmark)
    session.add_all([aaa, qqq])
    session.commit()
    for day in sessions:
        _price(session, aaa, day, 110.0 if day >= sessions[6] else 100.0)
        _price(session, qqq, day, 100.0)
    for index, published in enumerate(
        [datetime(2024, 1, 2, 15, 0, tzinfo=UTC), datetime(2024, 1, 16, 15, 0, tzinfo=UTC)]
    ):
        document = RawDocument(
            source="finnhub", external_id=f"n{index}", doc_type=DocType.news, raw_text="x",
            text="x", published_at=published,
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=aaa.id))
        session.add(
            JevReading(
                document_id=document.id, ticker_id=aaa.id, model_requested="typesafe/jev-1.13",
                model_resolved="typesafe/jev-1.13-20260917", question_set="q1",
                response_id="r", p_negative=0.1, p_neutral=0.2, p_positive=0.7,
                event_type="product", p_routine=0.1, answers={}, input_tokens=100,
                cost_usd=0.0, latency_ms=1,
            )
        )
    session.commit()
    closes = LegalCloses(WeekdaySessions().session_closes(sessions[0], sessions[-1]))
    samples, unlabeled = calibration_samples(session, ["AAA"], "QQQ", closes, sessions)
    # 2024-01-02 10:00 New York -> legal close 01-02; five sessions later (01-09) AAA is +10%.
    assert [(s.legal_close, s.label) for s in samples] == [(date(2024, 1, 2), "up")]
    assert unlabeled == 1  # 01-16 has no bar five sessions later
