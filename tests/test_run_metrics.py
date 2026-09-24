import math
from datetime import date

from pytest import approx

from signalbench.backtest.metrics import (
    TradeStats,
    cagr,
    max_drawdown,
    run_metrics,
    sharpe,
    trade_stats,
)
from signalbench.backtest.simulator import EquityPoint, SimulationResult, TradeRecord
from strategy_helpers import load_test_config

PARAMS = load_test_config().backtest


def _trade(entry: date, r: float, held: int = 5) -> TradeRecord:
    return TradeRecord(
        position_id=f"P-{entry}-{r}", symbol="AAA", setup="pullback", sector="Energy",
        signal_date=entry, entry_date=entry, exit_signal_date=entry, exit_date=entry,
        entry_price=100.0, exit_price=100.0 + r, initial_stop=99.0, units=1.0, r=r,
        pnl=r, reason="time", sessions_held=held,
    )


def test_trade_stats_by_hand() -> None:
    stats = trade_stats([_trade(date(2015, 1, 5), r, held) for r, held in ((2.0, 4), (-1.0, 10), (0.5, 7))])
    assert stats == TradeStats(trades=3, win_rate=approx(2 / 3), mean_r=approx(0.5),
                               median_r=approx(0.5), average_hold=approx(7.0))
    assert trade_stats([]) == TradeStats(0, 0.0, 0.0, 0.0, 0.0)


def test_sharpe_by_hand() -> None:
    # returns +10%, -10%, +10%: mean 1/30, sample stdev 0.2 / sqrt(3)
    assert sharpe([100.0, 110.0, 99.0, 108.9]) == approx(math.sqrt(3) / 6 * math.sqrt(252))
    assert sharpe([100.0, 100.0, 100.0]) == 0.0
    assert sharpe([100.0, 101.0]) == 0.0  # one return is not enough


def test_max_drawdown_and_cagr_by_hand() -> None:
    assert max_drawdown([100.0, 120.0, 90.0, 130.0, 117.0]) == approx(0.25)
    assert max_drawdown([100.0, 101.0]) == 0.0
    # 2020-01-01 to 2024-01-01 is 1461 days = 4.0 years of 365.25
    assert cagr(100.0, 146.41, date(2020, 1, 1), date(2024, 1, 1)) == approx(0.10)
    assert cagr(100.0, 50.0, date(2020, 1, 1), date(2020, 1, 1)) == 0.0


def test_run_metrics_splits_halves_and_recent_by_entry_date() -> None:
    trades = [
        _trade(date(2015, 3, 2), 1.0),
        _trade(date(2018, 12, 31), -0.5),  # last day of H1
        _trade(date(2019, 1, 2), 0.3),
        _trade(date(2026, 9, 15), 2.0),  # first recent day
    ]
    days = [date(2012, 1, 3), date(2012, 1, 4), date(2012, 1, 5), date(2012, 1, 6)]
    curve = [
        EquityPoint(days[0], 100.0, 100.0, 0),
        EquityPoint(days[1], 110.0, 50.0, 1),
        EquityPoint(days[2], 99.0, 50.0, 1),
        EquityPoint(days[3], 108.9, 108.9, 0),
    ]
    events: list[dict[str, object]] = [
        {"date": "2012-01-04", "event": "skip", "reason": "regime"},
        {"date": "2012-01-04", "event": "skip", "reason": "regime"},
        {"date": "2012-01-05", "event": "skip", "reason": "gap_up"},
        {"date": "2012-01-05", "event": "pause", "equity": 99.0, "peak": 110.0},
    ]
    result = SimulationResult(days[0], days[-1], curve, trades, events, open_positions=[])
    metrics = run_metrics(result, PARAMS)
    assert metrics.trades == 4
    assert metrics.mean_r == approx((1.0 - 0.5 + 0.3 + 2.0) / 4)
    assert (metrics.trades_h1, metrics.mean_r_h1) == (2, approx(0.25))
    assert (metrics.trades_h2, metrics.mean_r_h2) == (2, approx(1.15))
    assert (metrics.recent.trades, metrics.recent.mean_r) == (1, approx(2.0))
    assert metrics.total_return == approx(0.089)
    assert metrics.sharpe == approx(math.sqrt(3) / 6 * math.sqrt(252))
    assert metrics.max_drawdown == approx(0.1)
    assert metrics.exposure_pct == approx(0.5)
    assert metrics.pauses == 1
    assert metrics.skips_by_reason == {"gap_up": 1, "regime": 2}
    assert metrics.open_positions_at_end == 0
