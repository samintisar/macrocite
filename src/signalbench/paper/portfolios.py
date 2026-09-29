"""The pre-registered paper portfolios file, `data/paper_<n>.yaml` (spec 07, Portfolios)."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

PaperSetup = Literal["pullback", "breakout", "combined"]
SETUPS: tuple[PaperSetup, ...] = ("pullback", "breakout", "combined")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONFIG = re.compile(r"^data/strategy_[A-Za-z0-9_-]+\.yaml$")


class PaperFileError(ValueError):
    """The portfolios file is malformed. The message names the file and the entry."""


@dataclass(frozen=True)
class PortfolioSpec:
    name: str
    config: str  # repo-relative, forward slashes: data/strategy_<version>.yaml
    setup: PaperSetup


def paper_setup(value: object, where: str) -> PaperSetup:
    """A setup a paper portfolio may run: not Sentiment, whose Jev readings a paper run does
    not load."""
    if value not in SETUPS:
        raise PaperFileError(f"{where}: expected one of {', '.join(SETUPS)}")
    return value


def load_paper_file(path: Path) -> list[PortfolioSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    where = path.name
    if not isinstance(raw, dict) or set(raw) != {"portfolios"}:
        raise PaperFileError(f"{where}: expected exactly one top-level key, 'portfolios'")
    items = raw["portfolios"]
    if not isinstance(items, list) or not items:
        raise PaperFileError(f"{where}.portfolios: expected a non-empty list")
    specs: list[PortfolioSpec] = []
    for index, item in enumerate(items):
        at = f"{where}.portfolios[{index}]"
        if not isinstance(item, dict) or set(item) != {"name", "config", "setup"}:
            raise PaperFileError(f"{at}: expected exactly the keys name, config, setup")
        name, config, setup = item["name"], item["config"], item["setup"]
        if not isinstance(name, str) or not NAME.match(name):
            raise PaperFileError(f"{at}.name: expected lowercase letters, digits and dashes")
        if not isinstance(config, str) or not CONFIG.match(config):
            raise PaperFileError(f"{at}.config: expected data/strategy_<version>.yaml")
        specs.append(PortfolioSpec(name=name, config=config, setup=paper_setup(setup, f"{at}.setup")))
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise PaperFileError(f"{where}: duplicate names {duplicates}")
    return specs
