"""The pre-registered paper file (spec 11): which strategy runs forward, from which day, and
how it is reviewed. Committed on its own before `crypto paper start`; never edited. A new
idea is a new paper file with a new name."""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TypeGuard, cast, get_args

import yaml

from signalbench.strategy.config import SetupName

KEYS = frozenset({"name", "strategy", "strategy_sha256", "setup", "start", "review"})
REVIEW_KEYS = frozenset({"months", "max_late_days", "max_drawdown"})
HEX64 = re.compile(r"[0-9a-f]{64}")


class PaperError(ValueError):
    """A paper command or run refused; the message says why."""


@dataclass(frozen=True)
class ReviewRules:
    months: int  # the review date is start + months
    max_late_days: int  # days recorded more than 24 hours after their candle closed
    max_drawdown: float  # the worst peak-to-trough drop must stay under this


@dataclass(frozen=True)
class PaperFile:
    name: str
    strategy: str  # repo-relative path of the strategy config
    strategy_sha256: str
    setup: SetupName
    start: date
    review: ReviewRules

    @property
    def review_date(self) -> date:
        return add_months(self.start, self.review.months)


def add_months(day: date, months: int) -> date:
    """`day` moved by whole months, clamped to the month's last day (31 Jan + 1 = 28 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def load_paper_file(path: Path) -> PaperFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PaperError(f"{path.name}: {error}") from None
    return parse_paper_file(raw, path.name)


def parse_paper_file(raw: object, where: str) -> PaperFile:
    if not isinstance(raw, dict) or set(raw) != KEYS:
        raise PaperError(f"{where}: needs exactly the keys {', '.join(sorted(KEYS))}")
    name, strategy, sha = raw["name"], raw["strategy"], raw["strategy_sha256"]
    setup, start, review = raw["setup"], raw["start"], raw["review"]
    if not isinstance(name, str) or not name:
        raise PaperError(f"{where}: name must be a non-empty string")
    if not isinstance(strategy, str) or not strategy.endswith(".yaml"):
        raise PaperError(f"{where}: strategy must be a repo-relative .yaml path")
    if not isinstance(sha, str) or not HEX64.fullmatch(sha):
        raise PaperError(f"{where}: strategy_sha256 must be 64 lowercase hex characters")
    if setup not in get_args(SetupName):
        raise PaperError(f"{where}: unknown setup {setup!r}")
    if not isinstance(start, date) or isinstance(start, datetime):
        raise PaperError(f"{where}: start must be a date (YYYY-MM-DD)")
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise PaperError(
            f"{where}: review needs exactly the keys {', '.join(sorted(REVIEW_KEYS))}"
        )
    months, late, drawdown = review["months"], review["max_late_days"], review["max_drawdown"]
    if not _whole(months) or months < 1:
        raise PaperError(f"{where}: review.months must be a whole number of at least 1")
    if not _whole(late) or late < 0:
        raise PaperError(f"{where}: review.max_late_days must be a whole number of at least 0")
    if not isinstance(drawdown, int | float) or isinstance(drawdown, bool) or not 0 < drawdown < 1:
        raise PaperError(f"{where}: review.max_drawdown must be between 0 and 1")
    return PaperFile(
        name=name,
        strategy=strategy,
        strategy_sha256=sha,
        setup=cast(SetupName, setup),
        start=start,
        review=ReviewRules(months=months, max_late_days=late, max_drawdown=float(drawdown)),
    )


def _whole(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
