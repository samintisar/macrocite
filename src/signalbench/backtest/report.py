"""The stored JSON shape of a run and its markdown report (spec 02, Persistence and output)."""

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from signalbench.backtest.benchmarks import BenchmarkStats
from signalbench.backtest.metrics import RunMetrics
from signalbench.backtest.passbar import Criterion
from signalbench.backtest.simulator import SimulationResult
from signalbench.db.models import BacktestRun
from signalbench.strategy.config import BacktestParams

CAVEATS = (
    (
        "Survivorship: the universe is today's CDR list. Compare with the survivor "
        "benchmark, which has the same bias."
    ),
    "US prices stand in for CDR prices. CDR spreads enter only through the cost per side.",
    "Realized SEC Item 2.02 dates stand in for earnings dates known in advance.",
    "Liquidity is checked on US traded value only, because CDR history is short.",
)
PASS_BAR_ROWS = (
    ("trades", "Trades", ">="),
    ("mean_r", "Mean R after costs", ">"),
    ("mean_r_halves", "Mean R in both halves (worse half shown)", ">"),
    ("sharpe", "Sharpe vs QQQ buy-and-hold", ">="),
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return value


def metrics_payload(
    metrics: RunMetrics, benchmarks: list[BenchmarkStats], params: BacktestParams
) -> dict[str, Any]:
    payload: dict[str, Any] = _jsonable(asdict(metrics))
    payload["benchmarks"] = [asdict(stats) for stats in benchmarks]
    payload["h1_end"] = params.h1_end.isoformat()
    payload["h2_start"] = params.h2_start.isoformat()
    payload["recent_since"] = params.recent_since.isoformat()
    return payload


def pass_bar_payload(bar: dict[str, Criterion]) -> dict[str, Any]:
    return {name: asdict(criterion) for name, criterion in bar.items()}


def trade_log_payload(result: SimulationResult) -> dict[str, Any]:
    return {
        "trades": [_jsonable(asdict(trade)) for trade in result.trades],
        "events": _jsonable(result.events),
    }


def report_path(directory: Path, run_date: date, setup: str, jev_mode: str) -> Path:
    return directory / f"{run_date.isoformat()}-{setup}-{jev_mode}.md"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _header(run: BacktestRun) -> list[str]:
    lines = [
        f"# Backtest: {run.setup} (Jev {run.jev_mode})",
        "",
        f"**Strategy:** {run.strategy_version} · **Result:** {'PASS' if run.passed else 'FAIL'}",
        "",
    ]
    if run.setup == "combined":
        lines += ["**Information only:** a combined run does not change pass or fail.", ""]
    if run.strategy_version != "v1":
        lines += [
            f"**POST-HOC** ({run.strategy_version}): cannot overturn a v1 result on its own.",
            "",
        ]
    return [
        *lines,
        "| Field | Value |",
        "| --- | --- |",
        f"| Run id | `{run.id}` |",
        f"| Sessions | {run.start_date.isoformat()} to {run.end_date.isoformat()} |",
        f"| Run at (UTC) | {run.run_at.strftime('%Y-%m-%d %H:%M')} |",
        f"| config_sha256 | `{run.config_sha256}` |",
        f"| git_sha | `{run.git_sha}` |",
        f"| data_fingerprint | `{run.data_fingerprint}` |",
    ]


def _pass_bar(run: BacktestRun) -> list[str]:
    lines = [
        "## Pass bar",
        "",
        "| # | Criterion | Value | Threshold | Passed |",
        "| --- | --- | --- | --- | --- |",
    ]
    for number, (key, label, comparison) in enumerate(PASS_BAR_ROWS, start=1):
        row = run.pass_bar[key]
        passed = "yes" if row["passed"] else "no"
        lines.append(
            f"| {number} | {label} | {row['value']:.3f} "
            f"| {comparison} {row['threshold']:.3f} | {passed} |"
        )
    return lines


def _metrics(m: dict[str, Any]) -> list[str]:
    recent = m["recent"]
    return [
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Trades | {m['trades']} |",
        f"| Win rate | {_pct(m['win_rate'])} |",
        f"| Mean R | {m['mean_r']:.3f} |",
        f"| Median R | {m['median_r']:.3f} |",
        f"| Mean R, H1 (entries to {m['h1_end']}) | {m['mean_r_h1']:.3f} ({m['trades_h1']}) |",
        f"| Mean R, H2 (entries from {m['h2_start']}) | {m['mean_r_h2']:.3f} ({m['trades_h2']}) |",
        f"| Total return | {_pct(m['total_return'])} |",
        f"| CAGR | {_pct(m['cagr'])} |",
        f"| Sharpe (daily, sqrt 252, rf 0) | {m['sharpe']:.2f} |",
        f"| Max drawdown | {_pct(m['max_drawdown'])} |",
        f"| Exposure | {_pct(m['exposure_pct'])} |",
        f"| Average hold (sessions) | {m['average_hold']:.1f} |",
        f"| Pauses | {m['pauses']} |",
        f"| Open positions at the end | {m['open_positions_at_end']} |",
        "",
        f"## Trades entered on or after {m['recent_since']}",
        "",
        (
            f"Trades {recent['trades']} · win rate {_pct(recent['win_rate'])} · "
            f"mean R {recent['mean_r']:.3f} · median R {recent['median_r']:.3f}"
        ),
    ]


def _benchmarks(m: dict[str, Any]) -> list[str]:
    rows = [
        {
            "name": "This run",
            "total_return": m["total_return"],
            "cagr": m["cagr"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
        },
        *m["benchmarks"],
    ]
    return [
        "## Benchmarks (same sessions)",
        "",
        "| Series | Total return | CAGR | Sharpe | Max drawdown |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {row['name']} | {_pct(row['total_return'])} | {_pct(row['cagr'])} "
            f"| {row['sharpe']:.2f} | {_pct(row['max_drawdown'])} |"
            for row in rows
        ],
    ]


def _skips_and_caveats(m: dict[str, Any]) -> list[str]:
    skips: dict[str, int] = m["skips_by_reason"]
    rows = [f"| {reason} | {count} |" for reason, count in skips.items()] or ["| none | 0 |"]
    return [
        "## Skips by reason",
        "",
        "| Reason | Count |",
        "| --- | --- |",
        *rows,
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in CAVEATS],
    ]


def _trades(run: BacktestRun) -> list[str]:
    lines = [
        "## Trades",
        "",
        "| # | Symbol | Setup | Signal | Entry | Exit | Reason | Entry price | Exit price | R |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, trade in enumerate(run.trade_log["trades"], start=1):
        lines.append(
            f"| {number} | {trade['symbol']} | {trade['setup']} | {trade['signal_date']} "
            f"| {trade['entry_date']} | {trade['exit_date']} | {trade['reason']} "
            f"| {trade['entry_price']:.2f} | {trade['exit_price']:.2f} | {trade['r']:.2f} |"
        )
    return lines


def render_report(run: BacktestRun) -> str:
    sections = [
        _header(run),
        _pass_bar(run),
        _metrics(run.metrics),
        _benchmarks(run.metrics),
        _skips_and_caveats(run.metrics),
        _trades(run),
    ]
    return "\n\n".join("\n".join(section) for section in sections) + "\n"
