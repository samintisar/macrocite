from pathlib import Path

import pytest
from pytest import approx

from signalbench.strategy.spread import (
    SpreadSurveyError,
    cost_from_median_spread,
    load_spread_survey,
)


def _survey(tmp_path: Path, rows: list[tuple[str, float | None, float | None]]) -> Path:
    lines = ["readings:"]
    for symbol, bid, ask in rows:
        lines += [
            f"  - cdr_symbol: {symbol}",
            "    observed_at: 2026-09-24T10:45-07:00",
            f"    bid: {'' if bid is None else bid}",
            f"    ask: {'' if ask is None else ask}",
            "    bid_size:",
            "    ask_size:",
            "    note:",
        ]
    path = tmp_path / "cdr_spread_survey.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cost_formula_matches_the_spec_example() -> None:
    assert cost_from_median_spread(0.004) == 0.003  # spec: a 0.4% spread gives 0.3%
    assert cost_from_median_spread(0.001) == 0.002  # the 0.2% floor
    assert cost_from_median_spread(0.01) == 0.006


def test_median_of_complete_rows_sets_the_cost(tmp_path: Path) -> None:
    path = _survey(tmp_path, [
        ("ZNVD", 99.9, 100.1),  # 0.2%
        ("ZMSF", 99.8, 100.2),  # 0.4%
        ("ZAAP", 99.8, 100.2),  # 0.4%
        ("ZAMZ", 99.7, 100.3),  # 0.6%
        ("ZGOO", 99.5, 100.5),  # 1.0%
        ("ZMET", None, None),  # no quote shown: does not count
    ])
    survey = load_spread_survey(path)
    assert len(survey.readings) == 5 and survey.incomplete == 1
    assert survey.readings[0].spread_pct == approx(0.002)
    assert survey.median_spread == approx(0.004)
    assert survey.cost_per_side == 0.003


def test_median_of_exactly_one_percent_is_allowed(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(f"Z{i}", 99.5, 100.5) for i in range(5)])
    assert load_spread_survey(path).cost_per_side == 0.006


def test_median_above_one_percent_stops(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(f"Z{i}", 99.0, 101.0) for i in range(5)])  # 2%
    with pytest.raises(SpreadSurveyError, match="above 1%"):
        load_spread_survey(path)


def test_fewer_than_five_complete_rows_stops(tmp_path: Path) -> None:
    rows: list[tuple[str, float | None, float | None]] = [(f"Z{i}", 99.9, 100.1) for i in range(4)]
    rows.append(("ZBID", 99.9, None))  # bid without ask does not count
    with pytest.raises(SpreadSurveyError, match="has 4 readings with both bid and ask"):
        load_spread_survey(_survey(tmp_path, rows))


def test_the_empty_template_stops(tmp_path: Path) -> None:
    path = _survey(tmp_path, [(name, None, None) for name in ("ZNVD", "ZMSF", "ZAAP", "ZAMZ", "ZGOO")])
    with pytest.raises(SpreadSurveyError, match="has 0 readings"):
        load_spread_survey(path)


def test_ask_below_bid_is_rejected(tmp_path: Path) -> None:
    path = _survey(tmp_path, [("ZNVD", 100.2, 99.8)] + [(f"Z{i}", 99.9, 100.1) for i in range(5)])
    with pytest.raises(SpreadSurveyError, match="ZNVD: need 0 < bid <= ask"):
        load_spread_survey(path)


def test_five_readings_need_five_distinct_cdrs(tmp_path: Path) -> None:
    rows: list[tuple[str, float | None, float | None]] = [(f"Z{i}", 99.9, 100.1) for i in range(4)]
    rows.append(("Z0", 99.8, 100.2))  # a second reading of Z0 is not a fifth CDR
    with pytest.raises(SpreadSurveyError, match="across 4 distinct CDRs"):
        load_spread_survey(_survey(tmp_path, rows))
    rows.append(("Z4", 99.8, 100.2))
    survey = load_spread_survey(_survey(tmp_path, rows))
    assert len(survey.readings) == 6  # every complete reading counts toward the median


def _raw_survey(tmp_path: Path, body: str) -> Path:
    good = "".join(f"  - {{cdr_symbol: Z{i}, bid: 99.9, ask: 100.1}}\n" for i in range(5))
    path = tmp_path / "cdr_spread_survey.yaml"
    path.write_text("readings:\n" + good + body, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("  - ZNVD\n", r"readings\[5\]: expected a mapping"),
        ("  - {bid: 99.9, ask: 100.1}\n", r"readings\[5\]: missing cdr_symbol"),
        ("  - {cdr_symbol: '', bid: 99.9, ask: 100.1}\n", r"readings\[5\]: missing cdr_symbol"),
        ("  - {cdr_symbol: ZNVD, bid: abc, ask: 100.1}\n", r"readings\[5\] ZNVD: bid must be a number"),
        ("  - {cdr_symbol: ZNVD, bid: 99.9, ask: true}\n", r"readings\[5\] ZNVD: ask must be a number"),
    ],
)
def test_malformed_rows_name_the_row(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(SpreadSurveyError, match=message):
        load_spread_survey(_raw_survey(tmp_path, body))
