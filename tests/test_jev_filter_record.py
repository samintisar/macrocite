from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlmodel import Session

from signalbench.db.models import BacktestRun
from signalbench.jev.filter import ScoredTrade, decide_filter
from signalbench.jev.filter_record import (
    FilterFileError,
    FilterInputError,
    FilterRecord,
    load_filter_setting,
    load_v1_trades,
    render_filter_report,
    write_filter_file,
)

FIT_DAY = date(2019, 6, 3)
CONFIRM_DAY = date(2024, 6, 3)


def _run(
    session: Session,
    setup: str,
    trades: list[tuple[str, str, float]],
    *,
    sha: str = "c" * 64,
    run_at: datetime = datetime(2026, 9, 24, 21, 0, tzinfo=UTC),
    version: str = "v1",
    jev_mode: str = "off",
) -> BacktestRun:
    run = BacktestRun(
        strategy_version=version, config_sha256=sha, git_sha="abc123", setup=setup,
        jev_mode=jev_mode, start_date=date(2012, 1, 3), end_date=date(2026, 9, 24),
        data_fingerprint="d" * 64, metrics={}, pass_bar={}, passed=False,
        trade_log={
            "trades": [
                {"symbol": symbol, "signal_date": day, "r": r} for symbol, day, r in trades
            ],
            "events": [],
        },
        run_at=run_at,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_trades_come_from_the_latest_v1_jev_off_run_of_each_setup(session: Session) -> None:
    _run(session, "pullback", [("OLD", "2019-01-02", 9.0)], run_at=datetime(2026, 9, 1, tzinfo=UTC))
    pullback = _run(session, "pullback", [("AAA", "2019-06-03", 0.5)])
    breakout = _run(session, "breakout", [("BBB", "2024-06-03", -1.0), ("CCC", "2020-02-03", 2.0)])
    _run(session, "breakout", [("XXX", "2020-01-02", 5.0)], version="v2")  # post-hoc: ignored
    _run(session, "breakout", [("YYY", "2020-01-02", 5.0)], jev_mode="filter")  # ignored
    trades, runs = load_v1_trades(session)
    assert [(t.setup, t.symbol, t.signal_date, t.r) for t in trades] == [
        ("pullback", "AAA", date(2019, 6, 3), 0.5),
        ("breakout", "BBB", date(2024, 6, 3), -1.0),
        ("breakout", "CCC", date(2020, 2, 3), 2.0),
    ]
    assert [(run.setup, run.run_id) for run in runs] == [
        ("pullback", str(pullback.id)),
        ("breakout", str(breakout.id)),
    ]
    assert runs[0].config_sha256 == "c" * 64


def test_a_missing_setup_run_is_refused(session: Session) -> None:
    _run(session, "pullback", [])
    with pytest.raises(FilterInputError, match="No stored v1 jev-off run for breakout"):
        load_v1_trades(session)


def test_runs_with_two_config_hashes_are_refused(session: Session) -> None:
    _run(session, "pullback", [])
    _run(session, "breakout", [], sha="e" * 64)
    with pytest.raises(FilterInputError, match="more than one config_sha256"):
        load_v1_trades(session)


def _record(mode_on: bool) -> FilterRecord:
    fit = (
        [ScoredTrade("breakout", f"S{i}", FIT_DAY, -1.0, 0.85) for i in range(10)]
        + [ScoredTrade("breakout", f"K{i}", FIT_DAY, 0.5, None) for i in range(20)]
    )
    confirm_r = -0.5 if mode_on else 1.0
    confirm = (
        [ScoredTrade("pullback", f"S{i}", CONFIRM_DAY, confirm_r, 0.9) for i in range(10)]
        + [ScoredTrade("pullback", f"K{i}", CONFIRM_DAY, 0.3, None) for i in range(5)]
    )
    return FilterRecord(
        decision=decide_filter(fit + confirm),
        source_runs={"pullback": "run-p", "breakout": "run-b"},
        strategy_config_sha256="c" * 64,
        confirm_end=date(2026, 9, 24),
        readings=1234,
        builds={"typesafe/jev-1.13-20260917": 1234},
        decided_on=date(2026, 9, 25),
        report_path="reports/jev/2026-09-25-filter-decision.md",
    )


def test_an_on_decision_round_trips_through_the_filter_file(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=True))
    text = path.read_text(encoding="utf-8")
    assert "mode: 'on'\n" in text  # quoted: a bare `on` is a YAML 1.1 boolean
    assert "theta_block: 0.5\n" in text
    assert "report: reports/jev/2026-09-25-filter-decision.md" in text
    setting = load_filter_setting(path)
    assert (setting.mode, setting.theta_block) == ("on", 0.5)
    assert (setting.model_requested, setting.question_set) == ("typesafe/jev-1.13", "q1")


def test_an_information_only_decision_blocks_nothing(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=False))
    text = path.read_text(encoding="utf-8")
    assert "mode: information_only\n" in text
    assert "theta_fit: 0.5\n" in text and "theta_block: null\n" in text
    setting = load_filter_setting(path)
    assert (setting.mode, setting.theta_block) == ("information_only", None)


def test_the_filter_file_is_written_once(tmp_path: Path) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    write_filter_file(path, _record(mode_on=True))
    with pytest.raises(FilterFileError, match="already exists"):
        write_filter_file(path, _record(mode_on=False))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("mode: maybe\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n", "mode"),
        ("mode: on\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n", "quoted"),
        ("mode: 'on'\ntheta_block: null\nquestion_set: q1\nmodel_requested: m\n", "theta_block"),
        ("mode: 'on'\ntheta_block: 0.55\nquestion_set: q1\nmodel_requested: m\n", "theta_block"),
        (
            "mode: information_only\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n",
            "theta_block",
        ),
        (
            "mode: 'on'\ntheta_fit: 0.6\ntheta_block: 0.5\nquestion_set: q1\nmodel_requested: m\n",
            "theta_block",
        ),
        ("- not a mapping\n", "mapping"),
    ],
)
def test_a_malformed_filter_file_is_rejected(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "jev_filter_v1.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(FilterFileError, match=message):
        load_filter_setting(path)


def test_the_report_shows_the_rule_both_windows_and_the_caveats() -> None:
    text = render_filter_report(_record(mode_on=True))
    assert text.startswith("# Jev filter decision (spec 03)\n")
    assert "**Result:** ON, theta_block = 0.5" in text
    assert "| 0.5 | 10 | 20 | -1.000 | 0.500 | 1.500 | yes |" in text
    assert "| 0.9 | 0 | 30 | n/a | 0.000 | n/a | no |" in text
    assert "## Confirmation window: signals 2023-01-01 to 2026-09-24 (15 trades)" in text
    assert "| 0.5 | 10 | 5 | -0.500 | 0.300 |" in text
    assert "`run-p`" in text and "`run-b`" in text
    assert "cannot change the spec 02 v1 results" in text
    assert "effectively filings-only" in text
    info = render_filter_report(_record(mode_on=False))
    assert "**Result:** information only" in info
