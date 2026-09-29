"""Pre-registered strategy parameters (spec 02), loaded strictly from YAML."""

import hashlib
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal, cast, get_args

import yaml

SetupName = Literal["pullback", "breakout", "sentiment"]
SETUP_NAMES: tuple[SetupName, ...] = get_args(SetupName)
COST_PER_SIDE_FLOOR = 0.002
VEHICLE_COST_LIMIT = 0.05  # a cash vehicle's cost per side must be below 5%


@dataclass(frozen=True)
class PullbackParams:
    trend_sma_fast: int
    trend_sma_slow: int
    rsi_max: float
    stop_low_sessions: int
    stop_atr_mult: float
    target_r: float
    time_limit: int


@dataclass(frozen=True)
class BreakoutParams:
    trend_sma: int
    lookback: int
    volume_lookback: int
    volume_mult: float
    stop_atr_mult: float
    trail_atr_mult: float
    time_limit: int | None  # None: exit only on the trailing stop, earnings, or the run's end


@dataclass(frozen=True)
class SentimentParams:
    trend_sma: int
    stop_atr_mult: float
    target_r: float
    time_limit: int


@dataclass(frozen=True)
class CashVehicle:
    """Where idle cash waits between trades (spec 06), traded at the open like a stock."""

    symbol: str
    cost_per_side: float


@dataclass(frozen=True)
class BacktestParams:
    start: date
    h1_end: date
    h2_start: date
    recent_since: date
    min_trades: int
    min_mean_r: float


@dataclass(frozen=True)
class StrategyConfig:
    version: str
    start_equity: float
    risk_pct: float
    max_positions: int
    max_per_sector: int
    pause_drawdown: float
    auto_resume_sessions: int
    gap_up_limit: float
    cost_per_side: float
    setup_priority: tuple[SetupName, ...]
    enabled_setups: tuple[SetupName, ...]
    regime_symbol: str
    regime_sma: int
    atr_period: int
    rsi_period: int
    liquidity_min_traded_value: float
    liquidity_sessions: int
    earnings_blackout_sessions: int
    earnings_exit_sessions: int
    pullback: PullbackParams
    breakout: BreakoutParams
    sentiment: SentimentParams
    backtest: BacktestParams
    cash_vehicle: CashVehicle | None = None  # None: idle cash stays cash (v1)

    def with_setups(self, setups: tuple[SetupName, ...]) -> "StrategyConfig":
        """A copy that only fires `setups`, kept in `setup_priority` order."""
        unknown = set(setups) - set(self.setup_priority)
        if unknown:
            raise ValueError(f"Unknown setups: {sorted(unknown)}")
        ordered = tuple(name for name in self.setup_priority if name in setups)
        return replace(self, enabled_setups=ordered)

    def sma_periods(self) -> frozenset[int]:
        """Every SMA length any rule reads, so MarketView computes each one once."""
        return frozenset(
            {
                self.regime_sma,
                self.pullback.trend_sma_fast,
                self.pullback.trend_sma_slow,
                self.breakout.trend_sma,
                self.sentiment.trend_sma,
            }
        )


class ConfigError(ValueError):
    pass


class _Section:
    """One YAML mapping. Every key must be read exactly once; leftovers are an error."""

    def __init__(self, raw: object, where: str) -> None:
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: expected a mapping")
        self._raw = cast(dict[str, Any], raw)
        self._where = where
        self._used: set[str] = set()

    def _get(self, key: str) -> Any:
        if key not in self._raw:
            raise ConfigError(f"{self._where}: missing key {key!r}")
        self._used.add(key)
        return self._raw[key]

    def section(self, key: str) -> "_Section":
        return _Section(self._get(key), f"{self._where}.{key}")

    def has(self, key: str) -> bool:
        return key in self._raw

    def integer(self, key: str, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected an integer >= {minimum}")
        return value

    def integer_or_null(self, key: str, minimum: int = 1) -> int | None:
        """Like integer(), but an explicit null is allowed. The key must still be written."""
        if self._raw.get(key, 0) is None:
            self._used.add(key)
            return None
        return self.integer(key, minimum)

    def number(self, key: str, minimum: float = 0.0) -> float:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int | float) or value < minimum:
            raise ConfigError(f"{self._where}.{key}: expected a number >= {minimum}")
        return float(value)

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{self._where}.{key}: expected a non-empty string")
        return value

    def day(self, key: str) -> date:
        value = self._get(key)
        if not isinstance(value, date):
            raise ConfigError(f"{self._where}.{key}: expected a YYYY-MM-DD date")
        return value

    def setups(self, key: str) -> tuple[SetupName, ...]:
        value = self._get(key)
        if not isinstance(value, list) or sorted(value) != sorted(SETUP_NAMES):
            raise ConfigError(f"{self._where}.{key}: expected each of {list(SETUP_NAMES)} once")
        return tuple(cast(list[SetupName], value))

    def done(self) -> None:
        extra = sorted(set(self._raw) - self._used)
        if extra:
            raise ConfigError(f"{self._where}: unknown keys {extra}")


def parse_strategy_config(raw: object, where: str = "config") -> StrategyConfig:
    top = _Section(raw, where)
    regime = top.section("regime")
    indicators = top.section("indicators")
    liquidity = top.section("liquidity")
    earnings = top.section("earnings")
    setups = top.section("setups")
    pull = setups.section("pullback")
    brk = setups.section("breakout")
    sent = setups.section("sentiment")
    back = top.section("backtest")
    bar = back.section("pass_bar")
    priority = top.setups("setup_priority")
    vehicle = _cash_vehicle(top, where) if top.has("cash_vehicle") else None
    config = StrategyConfig(
        version=top.text("version"),
        start_equity=top.number("start_equity", minimum=1.0),
        risk_pct=top.number("risk_pct"),
        max_positions=top.integer("max_positions"),
        max_per_sector=top.integer("max_per_sector"),
        pause_drawdown=top.number("pause_drawdown"),
        auto_resume_sessions=top.integer("auto_resume_sessions"),
        gap_up_limit=top.number("gap_up_limit"),
        cost_per_side=top.number("cost_per_side", minimum=COST_PER_SIDE_FLOOR),
        setup_priority=priority,
        enabled_setups=priority,
        regime_symbol=regime.text("symbol"),
        regime_sma=regime.integer("sma"),
        atr_period=indicators.integer("atr_period"),
        rsi_period=indicators.integer("rsi_period"),
        liquidity_min_traded_value=liquidity.number("min_median_traded_value_usd"),
        liquidity_sessions=liquidity.integer("sessions"),
        earnings_blackout_sessions=earnings.integer("blackout_sessions"),
        earnings_exit_sessions=earnings.integer("exit_sessions"),
        pullback=PullbackParams(
            trend_sma_fast=pull.integer("trend_sma_fast"),
            trend_sma_slow=pull.integer("trend_sma_slow"),
            rsi_max=pull.number("rsi_max"),
            stop_low_sessions=pull.integer("stop_low_sessions"),
            stop_atr_mult=pull.number("stop_atr_mult"),
            target_r=pull.number("target_r"),
            time_limit=pull.integer("time_limit"),
        ),
        breakout=BreakoutParams(
            trend_sma=brk.integer("trend_sma"),
            lookback=brk.integer("lookback"),
            volume_lookback=brk.integer("volume_lookback"),
            volume_mult=brk.number("volume_mult"),
            stop_atr_mult=brk.number("stop_atr_mult"),
            trail_atr_mult=brk.number("trail_atr_mult"),
            time_limit=brk.integer_or_null("time_limit"),
        ),
        sentiment=SentimentParams(
            trend_sma=sent.integer("trend_sma"),
            stop_atr_mult=sent.number("stop_atr_mult"),
            target_r=sent.number("target_r"),
            time_limit=sent.integer("time_limit"),
        ),
        backtest=BacktestParams(
            start=back.day("start"),
            h1_end=back.day("h1_end"),
            h2_start=back.day("h2_start"),
            recent_since=back.day("recent_since"),
            min_trades=bar.integer("min_trades"),
            min_mean_r=bar.number("min_mean_r"),
        ),
        cash_vehicle=vehicle,
    )
    for section in (top, regime, indicators, liquidity, earnings, setups, pull, brk, sent, back, bar):
        section.done()
    if vehicle is not None and vehicle.symbol != config.regime_symbol:
        raise ConfigError(
            f"{where}.cash_vehicle.symbol: must be the regime symbol {config.regime_symbol!r} "
            f"(the only series besides the universe that a run loads), got {vehicle.symbol!r}"
        )
    if config.backtest.h2_start != config.backtest.h1_end + timedelta(days=1):
        raise ConfigError(
            f"{where}.backtest: h2_start must be the day after h1_end (contiguous halves), "
            f"got h1_end {config.backtest.h1_end} and h2_start {config.backtest.h2_start}"
        )
    return config


def _cash_vehicle(top: _Section, where: str) -> CashVehicle:
    section = top.section("cash_vehicle")
    vehicle = CashVehicle(
        symbol=section.text("symbol"), cost_per_side=section.number("cost_per_side")
    )
    section.done()
    if vehicle.cost_per_side >= VEHICLE_COST_LIMIT:
        raise ConfigError(
            f"{where}.cash_vehicle.cost_per_side: expected a number in "
            f"[0, {VEHICLE_COST_LIMIT}), got {vehicle.cost_per_side}"
        )
    return vehicle


def config_sha256(raw: bytes) -> str:
    """SHA-256 of the file with CRLF normalised to LF, so Windows and CI agree."""
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def load_strategy_config(path: Path) -> tuple[StrategyConfig, str]:
    raw = path.read_bytes()
    config = parse_strategy_config(yaml.safe_load(raw), where=path.name)
    return config, config_sha256(raw)
