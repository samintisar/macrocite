"""The CDR spread survey sets the backtest cost per side (overview, "Spread survey")."""

from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, cast

import yaml

MIN_COMPLETE_READINGS = 5  # distinct CDRs with both bid and ask
MAX_MEDIAN_SPREAD = 0.01
COST_FLOOR = 0.002
COST_ABOVE_HALF_SPREAD = 0.001


class SpreadSurveyError(ValueError):
    pass


@dataclass(frozen=True)
class SpreadReading:
    cdr_symbol: str
    bid: float
    ask: float

    @property
    def spread_pct(self) -> float:
        """(ask - bid) / midpoint."""
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2.0)


@dataclass(frozen=True)
class SpreadSurvey:
    readings: tuple[SpreadReading, ...]
    incomplete: int  # rows without both bid and ask; they do not count
    median_spread: float
    cost_per_side: float


def cost_from_median_spread(median_spread: float) -> float:
    """max(0.2%, median / 2 + 0.1%), rounded to 6 decimals for the YAML file."""
    return round(max(COST_FLOOR, median_spread / 2.0 + COST_ABOVE_HALF_SPREAD), 6)


def _price(row: dict[str, Any], key: str, where: str) -> float | None:
    value = row.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SpreadSurveyError(f"{where}: {key} must be a number, got {value!r}")
    return float(value)


def _symbol(item: object, index: int) -> tuple[dict[str, Any], str]:
    """The row as a mapping and its cdr_symbol, or a SpreadSurveyError naming the row."""
    if not isinstance(item, dict):
        raise SpreadSurveyError(f"readings[{index}]: expected a mapping, got {item!r}")
    row = cast(dict[str, Any], item)
    symbol = "" if row.get("cdr_symbol") is None else str(row["cdr_symbol"]).strip()
    if not symbol:
        raise SpreadSurveyError(f"readings[{index}]: missing cdr_symbol")
    return row, symbol


def load_spread_survey(path: Path) -> SpreadSurvey:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("readings") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise SpreadSurveyError(f"{path.name}: expected a top-level `readings:` list")
    readings: list[SpreadReading] = []
    incomplete = 0
    for index, item in enumerate(rows):
        row, symbol = _symbol(item, index)
        where = f"readings[{index}] {symbol}"
        bid, ask = _price(row, "bid", where), _price(row, "ask", where)
        if bid is None or ask is None:
            incomplete += 1
            continue
        if bid <= 0.0 or ask < bid:
            raise SpreadSurveyError(f"{where}: need 0 < bid <= ask, got {bid}/{ask}")
        readings.append(SpreadReading(symbol, bid, ask))
    distinct = len({reading.cdr_symbol for reading in readings})
    if distinct < MIN_COMPLETE_READINGS:
        raise SpreadSurveyError(
            f"{path.name} has {len(readings)} readings with both bid and ask across {distinct} "
            f"distinct CDRs; at least {MIN_COMPLETE_READINGS} distinct CDRs are needed. "
            "Record them during market hours first."
        )
    spread = float(median(reading.spread_pct for reading in readings))
    if spread > MAX_MEDIAN_SPREAD:
        raise SpreadSurveyError(
            f"Median CDR spread is {spread:.2%}, above 1%. Stop and revisit the instrument "
            "choice before any backtest (overview, Spread survey)."
        )
    return SpreadSurvey(
        readings=tuple(readings),
        incomplete=incomplete,
        median_spread=spread,
        cost_per_side=cost_from_median_spread(spread),
    )
