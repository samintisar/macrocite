from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Trade:
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal


def _first_trading_index_on_or_after(
    prices: list[tuple[date, Decimal]], on_or_after: date
) -> int | None:
    for index, (trading_date, _) in enumerate(prices):
        if trading_date >= on_or_after:
            return index
    return None


def _find_exit_index(
    prices: list[tuple[date, Decimal]],
    entry_index: int,
    entry_price: Decimal,
    holding_days: int,
    stop_loss: float | None,
    take_profit: float | None,
) -> int | None:
    target_index = entry_index + holding_days

    for index in range(entry_index + 1, len(prices)):
        price = prices[index][1]
        if stop_loss is not None:
            stop_price = entry_price * (Decimal(1) + Decimal(str(stop_loss)))
            if price <= stop_price:
                return index
        if take_profit is not None:
            profit_price = entry_price * (Decimal(1) + Decimal(str(take_profit)))
            if price >= profit_price:
                return index
        if index == target_index:
            return index
    return None


def _signal_published_at(signal: dict[str, object]) -> datetime:
    published_at = signal["published_at"]
    if not isinstance(published_at, datetime):
        msg = "signal published_at must be a datetime"
        raise TypeError(msg)
    return published_at


def build_trades(
    *,
    prices: list[tuple[date, Decimal]],
    signals: list[dict[str, object]],
    sentiment_threshold: float,
    holding_days: int,
    stop_loss: float | None,
    take_profit: float | None,
) -> list[Trade]:
    sorted_signals = sorted(signals, key=_signal_published_at)
    trades: list[Trade] = []
    position_open_until: date | None = None

    for signal in sorted_signals:
        sentiment = signal["sentiment"]
        published_at = _signal_published_at(signal)
        if not isinstance(sentiment, (int, float)):
            continue
        if sentiment <= sentiment_threshold:
            continue

        entry_index = _first_trading_index_on_or_after(
            prices, published_at.date()
        )
        if entry_index is None:
            continue

        entry_date, entry_price = prices[entry_index]
        if position_open_until is not None and entry_date <= position_open_until:
            continue

        exit_index = _find_exit_index(
            prices,
            entry_index,
            entry_price,
            holding_days,
            stop_loss,
            take_profit,
        )
        if exit_index is None:
            continue

        exit_date, exit_price = prices[exit_index]
        trades.append(
            Trade(
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=entry_price,
                exit_price=exit_price,
            )
        )
        position_open_until = exit_date

    return trades
