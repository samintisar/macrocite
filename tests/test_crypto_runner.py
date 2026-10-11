from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pytest import approx
from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import (
    last_complete_day,
    longest_gap,
    missing_days,
    run_crypto_backtest,
)
from signalbench.backtest.preregistration import RunRefusedError
from signalbench.db.models import BacktestRun, Price, Ticker, TickerKind
from signalbench.market.bars import AdjustedBar
from signalbench.market.calendar import DailySessions
from signalbench.strategy.config import (
    CashVehicle,
    StrategyConfig,
    load_strategy_config,
)
from strategy_helpers import make_bar

CONFIG_PATH = Path(__file__).parent / "fixtures" / "strategy_crypto_test.yaml"
DAYS = [date(2014, 1, 1) + timedelta(days=i) for i in range(300)]  # every calendar day
BREAKOUT = 260  # 2014-09-18: close 110 on double volume, after 260 days flat at 100
CRASH = 280  # close 100, below the stop: the exit fills at the next open
NOW = datetime(2014, 10, 28, 12, 0, tzinfo=UTC)  # the last complete day is DAYS[-1]


def _config() -> StrategyConfig:
    return load_strategy_config(CONFIG_PATH)[0]


def _btc() -> list[AdjustedBar]:
    bars = []
    for i, day in enumerate(DAYS):
        if i < BREAKOUT:
            bars.append(make_bar(day, 100.0, volume=1000))
        elif i == BREAKOUT:
            bars.append(make_bar(day, 110.0, volume=2000))
        elif i < CRASH:
            bars.append(make_bar(day, 110.0, volume=1000))
        else:
            bars.append(make_bar(day, 100.0, volume=1000))
    return bars


def _eth() -> list[AdjustedBar]:
    return [make_bar(day, 50.0, volume=1000) for day in DAYS]  # flat: never breaks out


def _store(session: Session, symbol: str, bars: list[AdjustedBar]) -> None:
    ticker = Ticker(symbol=symbol, company_name=symbol, sector="crypto", kind=TickerKind.crypto)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    for bar in bars:
        price = Decimal(str(bar.close))
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar.date,
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=price,
                adj_close=price,
                volume=bar.volume,
            )
        )
    session.commit()


@pytest.fixture
def seeded(session: Session) -> Session:
    _store(session, "XBTUSD", _btc())
    _store(session, "ETHUSD", _eth())
    return session


def _run(
    session: Session,
    tmp_path: Path,
    *,
    config: StrategyConfig | None = None,
    sha: str = "f" * 64,
    now: datetime = NOW,
) -> tuple[BacktestRun, Path]:
    return run_crypto_backtest(
        session,
        config=config or _config(),
        config_sha256=sha,
        calendar=DailySessions(),
        git_sha="abc123",
        run_date=date(2014, 10, 28),
        reports_dir=tmp_path,
        now=now,
    )


def _drop_eth_days(session: Session, days: list[date]) -> None:
    eth = session.exec(select(Ticker).where(Ticker.symbol == "ETHUSD")).one()
    rows = session.exec(
        select(Price).where(Price.ticker_id == eth.id, col(Price.date).in_(days))
    ).all()
    for row in rows:
        session.delete(row)
    session.commit()


def test_one_breakout_gives_one_hand_checked_trade_against_btc(
    seeded: Session, tmp_path: Path
) -> None:
    run, path = _run(seeded, tmp_path)
    assert (run.setup, run.jev_mode, run.strategy_version) == ("breakout", "off", "crypto-test")
    assert (run.start_date, run.end_date) == (date(2014, 9, 1), DAYS[-1])
    [trade] = run.trade_log["trades"]
    assert trade["symbol"] == "XBTUSD"
    assert (trade["signal_date"], trade["entry_date"]) == (
        DAYS[BREAKOUT].isoformat(),
        DAYS[BREAKOUT + 1].isoformat(),
    )
    assert (trade["exit_date"], trade["reason"]) == (DAYS[CRASH + 1].isoformat(), "stop")
    # ATR(14) on the breakout day is (13 x 2 + 11) / 14 = 37/14, so the planned risk is
    # 2 x 37/14 = 37/7. Entry fills at 110 x 1.004, the exit at 100 x 0.996.
    assert trade["r"] == approx((100 * 0.996 - 110 * 1.004) / (37 / 7))
    assert run.metrics["market"] == "crypto"
    assert [b["name"] for b in run.metrics["benchmarks"]] == ["BTC buy-and-hold"]
    assert run.metrics["benchmarks"][0]["total_return"] == approx(0.0)
    assert run.pass_bar["sharpe"]["threshold"] == run.metrics["benchmarks"][0]["sharpe"]
    assert run.metrics["missing_days"] == {"ETHUSD": [], "XBTUSD": []}
    assert run.passed is False  # one trade is far below 30
    assert seeded.exec(select(BacktestRun)).one().id == run.id
    assert path == tmp_path / "2014-10-28-crypto-test-breakout-off.md"
    text = path.read_text(encoding="utf-8")
    assert "**First test on crypto.**" in text
    assert "| 4 | Sharpe vs BTC buy-and-hold |" in text


def test_a_short_gap_is_allowed_and_listed(seeded: Session, tmp_path: Path) -> None:
    gap = [DAYS[270], DAYS[271]]
    _drop_eth_days(seeded, gap)
    run, _path = _run(seeded, tmp_path)
    assert run.metrics["missing_days"]["ETHUSD"] == [day.isoformat() for day in gap]
    assert len(run.trade_log["trades"]) == 1


def test_a_gap_longer_than_three_days_is_refused(seeded: Session, tmp_path: Path) -> None:
    _drop_eth_days(seeded, DAYS[270:274])
    with pytest.raises(RunRefusedError, match="ETHUSD \\(4 days in a row\\)"):
        _run(seeded, tmp_path)


def test_a_gap_before_the_run_starts_is_ignored(seeded: Session, tmp_path: Path) -> None:
    _drop_eth_days(seeded, DAYS[100:110])
    run, _path = _run(seeded, tmp_path)
    assert run.metrics["missing_days"]["ETHUSD"] == []


def test_series_must_end_on_the_last_complete_utc_day(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(RunRefusedError, match="do not end on 2014-10-28"):
        _run(seeded, tmp_path, now=NOW + timedelta(days=1))


def test_cost_must_be_krakens_top_fee(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(RunRefusedError, match="Kraken's top published fee"):
        _run(seeded, tmp_path, config=replace(_config(), cost_per_side=0.002))


def test_the_regime_must_be_btc(seeded: Session, tmp_path: Path) -> None:
    with pytest.raises(RunRefusedError, match="regime 'QQQ'"):
        _run(seeded, tmp_path, config=replace(_config(), regime_symbol="QQQ"))


def test_idle_cash_must_stay_cash(seeded: Session, tmp_path: Path) -> None:
    vehicle = CashVehicle(symbol="XBTUSD", cost_per_side=0.004)
    with pytest.raises(RunRefusedError, match="keeps idle cash as cash"):
        _run(seeded, tmp_path, config=replace(_config(), cash_vehicle=vehicle))


def test_btc_must_have_a_bar_on_the_first_day(seeded: Session, tmp_path: Path) -> None:
    config = _config()
    early = replace(config, backtest=replace(config.backtest, start=date(2013, 12, 1)))
    with pytest.raises(RunRefusedError, match="XBTUSD starts on 2014-01-01"):
        _run(seeded, tmp_path, config=early)


def test_a_three_day_gap_is_the_longest_allowed(seeded: Session, tmp_path: Path) -> None:
    _drop_eth_days(seeded, DAYS[270:273])
    run, _path = _run(seeded, tmp_path)
    assert len(run.metrics["missing_days"]["ETHUSD"]) == 3


def test_todays_candle_is_never_read(seeded: Session, tmp_path: Path) -> None:
    today = NOW.date()  # 2014-10-28, still trading at NOW
    for symbol in ("XBTUSD", "ETHUSD"):
        ticker = seeded.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
        price = Decimal("500.0")
        seeded.add(
            Price(
                ticker_id=ticker.id,
                date=today,
                open=price,
                high=price,
                low=price,
                close=price,
                adj_close=price,
                volume=1000,
            )
        )
    seeded.commit()
    run, _path = _run(seeded, tmp_path)
    assert run.end_date == DAYS[-1]


def test_a_changed_config_is_a_new_version(seeded: Session, tmp_path: Path) -> None:
    _run(seeded, tmp_path)
    with pytest.raises(RunRefusedError, match="already has runs"):
        _run(seeded, tmp_path, sha="e" * 64)


def test_both_symbols_must_be_loaded(session: Session, tmp_path: Path) -> None:
    _store(session, "XBTUSD", _btc())
    with pytest.raises(RunRefusedError, match="Not loaded: ETHUSD"):
        _run(session, tmp_path)


def test_missing_days_and_longest_gap() -> None:
    bars = [make_bar(date(2024, 1, d), 1.0) for d in (2, 3, 6, 7, 9)]
    days = missing_days(bars, date(2024, 1, 1), date(2024, 1, 10))
    assert days == [date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 10)]
    assert longest_gap(days) == 2
    assert longest_gap([]) == 0
    assert missing_days([], date(2024, 1, 1), date(2024, 1, 10)) == []


def test_last_complete_day_is_yesterday_in_utc() -> None:
    assert last_complete_day(datetime(2026, 10, 11, 0, 30, tzinfo=UTC)) == date(2026, 10, 10)
    vancouver = datetime(2026, 10, 10, 20, 0, tzinfo=ZoneInfo("America/Vancouver"))
    assert last_complete_day(vancouver) == date(2026, 10, 10)
    with pytest.raises(ValueError, match="timezone-aware"):
        last_complete_day(datetime(2026, 10, 11, 0, 30))  # noqa: DTZ001  # naive on purpose
