"""Shared by the spec 04 ledger tests: the hand-checked examples, tickers, prices, a ledger."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from signalbench.live.acb import SplitEvent, Trade

EXAMPLES = Path(__file__).parent / "fixtures" / "live_ledger_examples.yaml"


def load_example(name: str) -> dict[str, Any]:
    examples: dict[str, Any] = yaml.safe_load(EXAMPLES.read_text(encoding="utf-8"))
    return dict(examples[name])


def example_events(example: dict[str, Any]) -> tuple[list[Trade], list[SplitEvent]]:
    """An example's fills and splits, numbered in file order."""
    trades: list[Trade] = []
    splits: list[SplitEvent] = []
    for number, event in enumerate(example["events"], start=1):
        day: date = event["date"]
        if "split" in event:
            splits.append(SplitEvent(number, day, Decimal(event["split"])))
        else:
            trades.append(
                Trade(number, day, event["side"], Decimal(event["quantity"]),
                      Decimal(event["price"]), Decimal(event.get("fee", "0")))
            )
    return trades, splits
