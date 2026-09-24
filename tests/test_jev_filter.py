from datetime import date

from pytest import approx

from signalbench.jev.filter import (
    CONFIRM_START,
    FIT_END,
    FIT_START,
    THETA_GRID,
    ScoredTrade,
    TradeRow,
    decide_filter,
    score_trades,
    split,
)
from signalbench.strategy.readings import DocumentReading, JevReadingsView
from strategy_helpers import weekdays

FIT_DAY = date(2019, 6, 3)
CONFIRM_DAY = date(2024, 6, 3)


def _trades(day: date, count: int, r: float, p: float | None) -> list[ScoredTrade]:
    return [ScoredTrade("breakout", f"S{i}", day, r, p) for i in range(count)]


def _fit() -> list[ScoredTrade]:
    """theta 0.5 blocks 15 (mean R -1/3); 0.6, 0.7, 0.8 block 10 (mean R -1); 0.9 blocks 0."""
    return (
        _trades(FIT_DAY, 10, -1.0, 0.85)
        + _trades(FIT_DAY, 5, 1.0, 0.55)
        + _trades(FIT_DAY, 20, 0.5, None)  # no reading in the window: never blocked
    )


def test_the_pre_registered_grid_and_windows() -> None:
    assert THETA_GRID == (0.5, 0.6, 0.7, 0.8, 0.9)
    assert (FIT_START, FIT_END, CONFIRM_START) == (
        date(2016, 1, 1), date(2022, 12, 31), date(2023, 1, 1),
    )


def test_split_counts_blocked_and_kept_trades() -> None:
    row = split(_fit(), 0.5)
    assert (row.blocked, row.kept) == (15, 20)
    assert row.mean_r_blocked == approx(-1 / 3) and row.mean_r_kept == approx(0.5)
    assert row.difference == approx(0.5 + 1 / 3)
    empty = split(_fit(), 0.9)
    assert (empty.blocked, empty.mean_r_blocked, empty.difference) == (0, None, None)


def test_no_eligible_theta_means_information_only() -> None:
    trades = _trades(FIT_DAY, 9, -1.0, 0.95) + _trades(FIT_DAY, 30, 0.5, 0.1)
    decision = decide_filter(trades)
    assert decision.mode == "information_only"
    assert decision.theta is None and decision.confirm is None
    assert "no theta blocks at least 10 fit-window trades" in decision.reason
    assert [row.blocked for row in decision.fit] == [9, 9, 9, 9, 9]


def test_the_best_eligible_theta_wins_and_ties_go_to_the_lower_theta() -> None:
    confirm = _trades(CONFIRM_DAY, 10, -0.5, 0.65) + _trades(CONFIRM_DAY, 5, 0.3, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.theta == 0.6  # 0.6, 0.7 and 0.8 tie at +1.6; 0.5 gives +0.83
    assert [row.difference for row in decision.fit][:4] == [
        approx(0.5 + 1 / 3), approx(1.6), approx(1.6), approx(1.6),
    ]


def test_a_fit_that_confirms_turns_the_filter_on() -> None:
    confirm = _trades(CONFIRM_DAY, 10, -0.5, 0.65) + _trades(CONFIRM_DAY, 5, 0.3, 0.2)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "on"
    assert decision.theta == 0.6
    assert decision.confirm is not None
    assert (decision.confirm.blocked, decision.confirm.kept) == (10, 5)
    assert (decision.fit_trades, decision.confirm_trades) == (35, 15)


def test_a_fit_whose_blocked_trades_do_better_in_confirmation_is_information_only() -> None:
    confirm = _trades(CONFIRM_DAY, 10, 1.0, 0.65) + _trades(CONFIRM_DAY, 5, 0.0, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "information_only"
    assert decision.theta == 0.6  # still reported, but not applied
    assert "blocked mean R 1.000 is not below kept mean R 0.000" in decision.reason


def test_too_few_blocked_confirmation_trades_is_information_only() -> None:
    confirm = _trades(CONFIRM_DAY, 9, -2.0, 0.95) + _trades(CONFIRM_DAY, 5, 0.3, None)
    decision = decide_filter(_fit() + confirm)
    assert decision.mode == "information_only"
    assert "only 9 confirmation trades blocked" in decision.reason


def test_window_edges() -> None:
    edges = (
        _trades(date(2015, 12, 31), 10, -1.0, 0.95)  # before the fit window: ignored
        + _trades(FIT_START, 10, -1.0, 0.95)
        + _trades(FIT_END, 1, 0.5, None)
        + _trades(CONFIRM_START, 3, 0.1, None)
    )
    decision = decide_filter(edges)
    assert (decision.fit_trades, decision.confirm_trades) == (11, 3)


def test_trades_are_scored_with_the_readings_view_block_window() -> None:
    sessions = weekdays(date(2024, 1, 1), 30)
    view = JevReadingsView(
        {
            "AAA": [
                DocumentReading(sessions[5], 0.7, 0.2, 0.1, 0.1, "legal", "d1"),
                DocumentReading(sessions[20], 0.9, 0.05, 0.05, 0.1, "legal", "d2"),
            ]
        },
        sessions,
        block_theta=None,
    )
    rows = [
        TradeRow("pullback", "AAA", sessions[14], 0.4),  # d1 is 10 sessions back: inside
        TradeRow("pullback", "AAA", sessions[15], 0.4),  # d1 is 11 sessions back: outside
        TradeRow("breakout", "BBB", sessions[14], -1.0),
    ]
    assert [t.max_p_negative for t in score_trades(rows, view.max_p_negative)] == [0.7, None, None]
