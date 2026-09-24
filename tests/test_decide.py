import random
from dataclasses import replace
from datetime import date

import pytest
from pytest import approx

from signalbench.market.bars import AdjustedBar
from signalbench.strategy.decide import decide
from signalbench.strategy.decision import Decision, ExitOrder, Skip, StopUpdate
from signalbench.strategy.market_view import LookAheadError, MarketView
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    empty_portfolio,
    load_test_config,
    make_bar,
    make_market,
    make_position,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

CONFIG = load_test_config()
DAYS = weekdays(date(2023, 1, 2), 270)
SIGNAL = 251  # pullback_closes() dips on index 250 and 251
QQQ_UP = trend_bars(DAYS, 300.0, 0.5)
NULL = NullReadingsView()


class FakeReadings:
    def __init__(self, blocked: bool = False, catalyst: bool = False, trigger: bool = False) -> None:
        self._blocked, self._catalyst, self._trigger = blocked, catalyst, trigger

    def blocked(self, symbol: str, as_of: date) -> bool:
        return self._blocked

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return self._catalyst

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return self._trigger


def _market(benchmark: list[AdjustedBar] = QQQ_UP, **kwargs: object) -> MarketView:
    bars = {"AAA": series(DAYS, pullback_closes(len(DAYS)))}
    return make_market(bars, benchmark, DAYS, CONFIG, **kwargs)


def test_pullback_entry_with_stop_and_capped_size() -> None:
    decision = decide(DAYS[SIGNAL], _market(), NULL, empty_portfolio(100.0), CONFIG)
    assert decision.exits == [] and decision.skips == []
    [entry] = decision.entries
    assert (entry.symbol, entry.setup, entry.rank, entry.catalyst) == ("AAA", "pullback", 1, False)
    assert entry.signal_close == approx(146.0)
    # min(low of last 3 sessions) = 145; ATR(14) = (2 * 13 + 4) / 14; stop = 145 - 0.5 * ATR
    assert entry.stop == approx(145.0 - 0.5 * (30 / 14))
    assert (entry.target_r, entry.time_limit) == (2.0, 10)
    # risk units 2 / 2.07 = 0.97 units; the equity/3 cap wins: 33.33 / 146
    assert entry.units == approx(100.0 / 3 / 146.0)
    assert entry.risk_amount == approx(entry.units * (146.0 - entry.stop))


def test_no_signal_the_day_before() -> None:
    assert decide(DAYS[SIGNAL - 1], _market(), NULL, empty_portfolio(100.0), CONFIG) == Decision()


def test_regime_off_skips() -> None:
    market = _market(benchmark=trend_bars(DAYS, 300.0, -0.5))
    decision = decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG)
    assert decision.entries == []
    assert decision.skips == [Skip("AAA", "pullback", "regime")]


def test_paused_skips() -> None:
    paused = replace(empty_portfolio(100.0), paused=True, paused_since=DAYS[SIGNAL - 3])
    decision = decide(DAYS[SIGNAL], _market(), NULL, paused, CONFIG)
    assert decision.skips == [Skip("AAA", "pullback", "paused")]


def test_earnings_blackout_skips_for_d1_to_d3_only() -> None:
    blackout = _market(earnings={"AAA": [DAYS[SIGNAL + 3]]})
    decision = decide(DAYS[SIGNAL], blackout, NULL, empty_portfolio(100.0), CONFIG)
    assert decision.skips == [Skip("AAA", "pullback", "earnings_blackout")]
    later = _market(earnings={"AAA": [DAYS[SIGNAL + 4]]})
    assert len(decide(DAYS[SIGNAL], later, NULL, empty_portfolio(100.0), CONFIG).entries) == 1


def test_held_symbol_skips() -> None:
    portfolio = replace(empty_portfolio(100.0), positions=(make_position("P00001", "AAA"),))
    decision = decide(DAYS[SIGNAL], _market(), NULL, portfolio, CONFIG)
    assert decision.entries == []
    assert decision.skips == [Skip("AAA", "pullback", "held")]


def test_blocked_skips_and_catalyst_is_carried() -> None:
    market = _market()
    blocked = decide(DAYS[SIGNAL], market, FakeReadings(blocked=True), empty_portfolio(100.0), CONFIG)
    assert blocked.skips == [Skip("AAA", "pullback", "blocked")]
    tagged = decide(DAYS[SIGNAL], market, FakeReadings(catalyst=True), empty_portfolio(100.0), CONFIG)
    assert tagged.entries[0].catalyst is True


def test_illiquid_symbol_is_ignored_without_a_skip() -> None:
    thin = [replace(bar, traded_value=1_000_000.0) for bar in series(DAYS, pullback_closes(len(DAYS)))]
    market = make_market({"AAA": thin}, QQQ_UP, DAYS, CONFIG)
    assert decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG) == Decision()


def test_sentiment_fires_only_when_enabled_and_triggered() -> None:
    closes = [100.0 + 0.2 * i for i in range(len(DAYS))]
    bars = series(DAYS, closes)
    bars[260] = make_bar(DAYS[260], closes[260] + 3.0)  # close above the prior high
    market = make_market({"AAA": bars}, QQQ_UP, DAYS, CONFIG)
    sentiment_only = CONFIG.with_setups(("sentiment",))
    fired = decide(DAYS[260], market, FakeReadings(trigger=True), empty_portfolio(100.0), sentiment_only)
    assert [e.setup for e in fired.entries] == ["sentiment"]
    assert decide(DAYS[260], market, NULL, empty_portfolio(100.0), sentiment_only).entries == []
    price_only = CONFIG.with_setups(("pullback", "breakout"))
    assert decide(DAYS[260], market, FakeReadings(trigger=True), empty_portfolio(100.0), price_only).entries == []


def test_exit_and_trailing_stop_updates() -> None:
    market = _market()
    stopped = make_position("P00001", "AAA", stop=146.5)
    trailing = make_position(
        "P00002", "AAA", setup="breakout", stop=130.0, target=None, time_limit=30, highest_close=149.8
    )
    portfolio = replace(empty_portfolio(100.0), positions=(trailing, stopped))
    decision = decide(DAYS[SIGNAL], market, NULL, portfolio, CONFIG)
    assert decision.exits == [ExitOrder("P00001", "AAA", "stop")]
    atr = 30 / 14
    assert decision.stop_updates == [StopUpdate("P00002", 130.0, approx(149.8 - 3 * atr))]


def test_earnings_exit_two_sessions_ahead() -> None:
    market = _market(earnings={"AAA": [DAYS[SIGNAL + 2]]})
    held = replace(empty_portfolio(100.0), positions=(make_position("P00001", "AAA"),))
    assert decide(DAYS[SIGNAL], market, NULL, held, CONFIG).exits == [
        ExitOrder("P00001", "AAA", "earnings")
    ]
    three_ahead = _market(earnings={"AAA": [DAYS[SIGNAL + 3]]})
    assert decide(DAYS[SIGNAL], three_ahead, NULL, held, CONFIG).exits == []


def _random_walk(rng: random.Random, days: list[date]) -> list[AdjustedBar]:
    bars: list[AdjustedBar] = []
    close = 100.0
    for day in days:
        close = max(5.0, close * (1 + rng.gauss(0.0008, 0.02)))
        volume = int(1_000_000 * (3.0 if rng.random() < 0.05 else 1.0))
        spread = close * 0.01
        bars.append(make_bar(day, close, open_=close * (1 + rng.gauss(0, 0.005)),
                             high=close + spread, low=close - spread, volume=volume))
    return bars


def _truncate(bars: list[AdjustedBar], as_of: date) -> list[AdjustedBar]:
    return [bar for bar in bars if bar.date <= as_of]


def test_decide_on_truncated_data_equals_decide_on_full_data() -> None:
    rng = random.Random(20260924)
    days = weekdays(date(2021, 1, 4), 420)
    symbols = {name: _random_walk(rng, days) for name in ("AAA", "BBB", "CCC", "DDD")}
    sectors = {"AAA": "Energy", "BBB": "Energy", "CCC": "Energy", "DDD": "Utilities"}
    earnings = {"AAA": [days[300], days[350]], "CCC": [days[333]]}
    qqq = trend_bars(days, 300.0, 0.3)
    full = make_market(symbols, qqq, days, CONFIG, sectors=sectors, earnings=earnings)
    held = make_position("P00001", "BBB", sector="Energy", setup="breakout", stop=1.0,
                         target=None, time_limit=30, highest_close=150.0, sessions_held=3)
    portfolio = replace(empty_portfolio(100.0), positions=(held,))
    checked = rng.sample(range(260, 415), 25)
    non_empty = 0
    for index in checked:
        as_of = days[index]
        truncated = make_market(
            {name: _truncate(bars, as_of) for name, bars in symbols.items()},
            _truncate(qqq, as_of), days, CONFIG, sectors=sectors, earnings=earnings,
        )
        expected = decide(as_of, full, NULL, portfolio, CONFIG)
        assert decide(as_of, truncated, NULL, portfolio, CONFIG) == expected
        non_empty += expected != Decision(stop_updates=expected.stop_updates)
    assert non_empty > 0  # the sample exercised at least one entry, exit, or skip


def test_decide_is_deterministic() -> None:
    market = _market()
    first = decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG)
    assert decide(DAYS[SIGNAL], market, NULL, empty_portfolio(100.0), CONFIG) == first


def test_direct_future_access_raises() -> None:
    with pytest.raises(LookAheadError):
        _market().at(DAYS[SIGNAL]).snapshot("AAA", DAYS[SIGNAL + 1])
