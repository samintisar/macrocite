"""Inputs and outputs of the filter decision: the stored v1 trade logs, `data/jev_filter_v1.yaml`,
and the markdown report (spec 03, Filter decision). `data/strategy_v1.yaml` is never touched."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml
from sqlmodel import Session, col, select

from signalbench.db.models import BacktestRun
from signalbench.jev.filter import (
    CONFIRM_START,
    FIT_END,
    FIT_START,
    MIN_BLOCKED,
    THETA_GRID,
    FilterDecision,
    FilterMode,
    Split,
    TradeRow,
)
from signalbench.jev.questions import MODEL, QUESTION_SET

FILTER_SETUPS = ("pullback", "breakout")
FILE_HEADER = (
    "# Jev filter decision (spec 03, pre-registered rule), written once by\n"
    "# `signalbench jev fit-filter --write`. Do not edit: a different decision needs a new\n"
    "# question set. `signalbench backtest run --jev filter` reads mode and theta_block.\n"
)


class FilterInputError(ValueError):
    """The stored v1 runs cannot feed the filter decision. The message says why."""


class FilterFileError(ValueError):
    """`data/jev_filter_v1.yaml` is missing, malformed, or already written."""


@dataclass(frozen=True)
class SourceRun:
    setup: str
    run_id: str
    config_sha256: str
    end_date: date


def load_v1_trades(session: Session) -> tuple[list[TradeRow], list[SourceRun]]:
    """Closed trades of the latest stored v1 jev-off run of Pullback and of Breakout.

    Refuses when either run is missing, or when the stored v1 jev-off runs of these setups
    used more than one config_sha256.
    """
    runs = session.exec(
        select(BacktestRun)
        .where(
            BacktestRun.strategy_version == "v1",
            BacktestRun.jev_mode == "off",
            col(BacktestRun.setup).in_(FILTER_SETUPS),
        )
        .order_by(col(BacktestRun.run_at))
    ).all()
    latest = {run.setup: run for run in runs}  # later runs overwrite earlier ones
    missing = [setup for setup in FILTER_SETUPS if setup not in latest]
    if missing:
        raise FilterInputError(
            f"No stored v1 jev-off run for {', '.join(missing)}. Run "
            "`signalbench backtest run --setup <setup>` first (spec 02)."
        )
    hashes = sorted({run.config_sha256 for run in runs})
    if len(hashes) > 1:
        raise FilterInputError(
            "The stored v1 jev-off runs used more than one config_sha256 "
            f"({', '.join(sha[:12] for sha in hashes)}); the filter needs one pre-registered config."
        )
    trades: list[TradeRow] = []
    sources: list[SourceRun] = []
    for setup in FILTER_SETUPS:
        run = latest[setup]
        sources.append(SourceRun(setup, str(run.id), run.config_sha256, run.end_date))
        for trade in run.trade_log["trades"]:
            trades.append(
                TradeRow(
                    setup=setup,
                    symbol=str(trade["symbol"]),
                    signal_date=date.fromisoformat(str(trade["signal_date"])),
                    r=float(trade["r"]),
                )
            )
    return trades, sources


@dataclass(frozen=True)
class FilterRecord:
    decision: FilterDecision
    source_runs: dict[str, str]  # setup -> run id
    strategy_config_sha256: str
    confirm_end: date
    readings: int
    builds: dict[str, int]  # resolved model build -> readings
    decided_on: date
    report_path: str  # repo-relative


def _row(split: Split | None, trades: int) -> dict[str, Any] | None:
    if split is None:
        return None
    return {
        "theta": split.theta,
        "trades": trades,
        "blocked": split.blocked,
        "kept": split.kept,
        "mean_r_blocked": None if split.mean_r_blocked is None else round(split.mean_r_blocked, 6),
        "mean_r_kept": None if split.mean_r_kept is None else round(split.mean_r_kept, 6),
    }


def filter_file_payload(record: FilterRecord) -> dict[str, Any]:
    decision = record.decision
    chosen = next((row for row in decision.fit if row.theta == decision.theta), None)
    return {
        "question_set": QUESTION_SET,
        "model_requested": MODEL,
        "mode": decision.mode,
        "theta_fit": decision.theta,
        "theta_block": decision.theta if decision.mode == "on" else None,
        "reason": decision.reason,
        "fit_window": {"start": FIT_START, "end": FIT_END},
        "confirm_window": {"start": CONFIRM_START, "end": record.confirm_end},
        "fit": _row(chosen, decision.fit_trades),
        "confirm": _row(decision.confirm, decision.confirm_trades),
        "source_runs": dict(record.source_runs),
        "strategy_config_sha256": record.strategy_config_sha256,
        "readings": record.readings,
        "builds": dict(record.builds),
        "decided_on": record.decided_on,
        "report": record.report_path,
    }


def write_filter_file(path: Path, record: FilterRecord) -> None:
    """Write the decision once. An existing file is never overwritten."""
    if path.exists():
        raise FilterFileError(
            f"{path.name} already exists. The filter decision is made once; a new decision "
            "needs a new question set (spec 03)."
        )
    body = yaml.safe_dump(filter_file_payload(record), sort_keys=False, allow_unicode=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FILE_HEADER + body, encoding="utf-8")


@dataclass(frozen=True)
class FilterSetting:
    mode: FilterMode
    theta_block: float | None
    model_requested: str
    question_set: str


def load_filter_setting(path: Path) -> FilterSetting:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise FilterFileError(f"{path.name}: expected a mapping")
    data = cast(dict[str, Any], raw)
    mode = data.get("mode")
    if mode not in ("on", "information_only"):
        raise FilterFileError(
            f"{path.name}: mode must be 'on' (quoted) or information_only, got {mode!r}"
        )
    theta = data.get("theta_block")
    if mode == "on" and theta not in THETA_GRID:
        raise FilterFileError(f"{path.name}: theta_block must be one of {THETA_GRID} when on")
    if mode == "information_only" and theta is not None:
        raise FilterFileError(f"{path.name}: theta_block must be null when information_only")
    model, questions = data.get("model_requested"), data.get("question_set")
    if not isinstance(model, str) or not isinstance(questions, str):
        raise FilterFileError(f"{path.name}: model_requested and question_set must be strings")
    return FilterSetting(
        mode=cast(FilterMode, mode),
        theta_block=None if theta is None else float(theta),
        model_requested=model,
        question_set=questions,
    )


def _r(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def render_filter_report(record: FilterRecord) -> str:
    decision = record.decision
    builds = ", ".join(f"{build} ({count})" for build, count in sorted(record.builds.items()))
    if decision.mode == "on":
        result = f"ON, theta_block = {decision.theta}"
    else:
        result = "information only (nothing is blocked)"
    lines = [
        "# Jev filter decision (spec 03)",
        "",
        f"**Result:** {result}",
        "",
        f"**Why:** {decision.reason}.",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Decided on | {record.decided_on.isoformat()} |",
        f"| Question set / model requested | {QUESTION_SET} / {MODEL} |",
        f"| Readings available | {record.readings} |",
        f"| Resolved builds | {builds} |",
        *[
            f"| Source run ({setup}, v1, Jev off) | `{run}` |"
            for setup, run in record.source_runs.items()
        ],
        f"| strategy_v1 config_sha256 | `{record.strategy_config_sha256}` |",
        "",
        "## Rule (pre-registered)",
        "",
        (
            "Each trade gets the max `p_negative` among the symbol's documents whose legal close "
            "falls in the 10 sessions ending on its signal date (no reading: never blocked). A "
            f"theta is eligible when it blocks at least {MIN_BLOCKED} fit-window trades and keeps "
            "at least one. The eligible theta with the largest (kept mean R - blocked mean R) is "
            "chosen; ties go to the lower theta. The filter is ON only if, in the confirmation "
            f"window, at least {MIN_BLOCKED} trades are blocked and their mean R is below the "
            "kept mean R."
        ),
        "",
        (
            f"## Fit window: signals {FIT_START.isoformat()} to {FIT_END.isoformat()} "
            f"({decision.fit_trades} trades)"
        ),
        "",
        "| theta | Blocked | Kept | Mean R blocked | Mean R kept | Kept - blocked | Eligible |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in decision.fit:
        eligible = "yes" if row.blocked >= MIN_BLOCKED and row.kept > 0 else "no"
        lines.append(
            f"| {row.theta} | {row.blocked} | {row.kept} | {_r(row.mean_r_blocked)} "
            f"| {_r(row.mean_r_kept)} | {_r(row.difference)} | {eligible} |"
        )
    lines += [
        "",
        (
            f"## Confirmation window: signals {CONFIRM_START.isoformat()} to "
            f"{record.confirm_end.isoformat()} ({decision.confirm_trades} trades)"
        ),
        "",
    ]
    check = decision.confirm
    if check is None:
        lines.append("Not run: no theta was eligible.")
    else:
        lines += [
            "| theta | Blocked | Kept | Mean R blocked | Mean R kept |",
            "| --- | --- | --- | --- | --- |",
            (
                f"| {check.theta} | {check.blocked} | {check.kept} "
                f"| {_r(check.mean_r_blocked)} | {_r(check.mean_r_kept)} |"
            ),
        ]
    lines += [
        "",
        "## Notes",
        "",
        (
            "- A run with `--jev filter` is information only: it cannot change the spec 02 v1 "
            "results (Pullback and Breakout both failed their pass bar)."
        ),
        (
            "- Finnhub news covers only about the last year, so the fit window is effectively "
            "filings-only."
        ),
        (
            "- The theta grid, the windows, and the 10-trade minimum were fixed before this run. "
            "These results must not be used to change them."
        ),
    ]
    return "\n".join(lines) + "\n"
