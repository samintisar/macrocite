"""The stored JSON shape of a run and its markdown report (spec 02, Persistence and output)."""

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from signalbench.backtest.benchmarks import BenchmarkStats
from signalbench.backtest.metrics import RunMetrics, TradeStats, VehicleStats
from signalbench.backtest.passbar import Criterion
from signalbench.backtest.simulator import SimulationResult
from signalbench.db.models import BacktestRun
from signalbench.jev.questions import JEV_RELEASE
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
JEV_CAVEATS = (
    (
        "Jev's training cutoff is unpublished, so results before 2026-09-15 may be optimistic. "
        "Trades signalled on or after 2026-09-15 are the only fully out-of-sample ones."
    ),
    "Finnhub news covers only about the last year; earlier readings come from 8-K filings only.",
)
QQQ_CAVEATS = (
    "Post-hoc: the ideas came from looking at v1's results on the same data.",
    (
        "QQQ's 2012–2026 run was exceptional; holding more QQQ helps less or hurts if the next "
        "decade differs."
    ),
    (
        "Taxes are not modeled. In a non-registered account each QQQ switch is a disposition, "
        "and selling at a loss and rebuying within 30 days can be a superficial loss. "
        "Fractional units of the ETF are assumed."
    ),
    (
        "QQQ's adjusted prices stand in for the fund actually used, a CAD-listed, CAD-hedged "
        "Nasdaq-100 ETF, the same way US prices stand in for the hedged CDRs."
    ),
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


def jev_payload(
    *,
    model_requested: str,
    question_set: str,
    readings: int,
    builds: dict[str, int],
    theta_block: float | None,
    information_only: bool,
    out_of_sample: TradeStats,
) -> dict[str, Any]:
    """Spec 03 facts stored under metrics["jev"] for Sentiment and --jev filter runs."""
    return {
        "model_requested": model_requested,
        "question_set": question_set,
        "readings": readings,
        "builds": dict(sorted(builds.items())),
        "theta_block": theta_block,
        "information_only": information_only,
        "out_of_sample_since": JEV_RELEASE.isoformat(),
        "out_of_sample": asdict(out_of_sample),
    }


def cash_vehicle_payload(
    stats: VehicleStats, sensitivity: BenchmarkStats, sensitivity_cost: float
) -> dict[str, Any]:
    """Spec 06 facts stored under metrics["cash_vehicle"] for runs with a cash vehicle. The
    sensitivity run (the same config at `sensitivity_cost` per switch) is information only."""
    payload: dict[str, Any] = asdict(stats)
    payload["sensitivity"] = {
        "cost_per_side": sensitivity_cost,
        "total_return": sensitivity.total_return,
        "cagr": sensitivity.cagr,
        "sharpe": sensitivity.sharpe,
        "max_drawdown": sensitivity.max_drawdown,
    }
    return payload


def pass_bar_payload(bar: dict[str, Criterion]) -> dict[str, Any]:
    return {name: asdict(criterion) for name, criterion in bar.items()}


def trade_log_payload(result: SimulationResult) -> dict[str, Any]:
    return {
        "trades": [_jsonable(asdict(trade)) for trade in result.trades],
        "open_at_end": [_jsonable(asdict(position)) for position in result.open_at_end],
        "events": _jsonable(result.events),
    }


def report_path(
    directory: Path, run_date: date, setup: str, jev_mode: str, version: str = "v1"
) -> Path:
    """<date>-<setup>-<jev>.md for v1; other versions add theirs (spec 06), so variants run on
    the same day get their own files: <date>-<version>-<setup>-<jev>.md."""
    name = f"{setup}-{jev_mode}" if version == "v1" else f"{version}-{setup}-{jev_mode}"
    return directory / f"{run_date.isoformat()}-{name}.md"


def result_label(run: BacktestRun) -> str:
    """PASS or FAIL; a combined run is information only and never passes or fails."""
    if run.setup == "combined":
        return "INFO"
    return "PASS" if run.passed else "FAIL"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cost(value: float) -> str:
    return f"{value * 100:.2f}%"


def _header(run: BacktestRun) -> list[str]:
    result = result_label(run)
    if run.jev_mode == "filter":
        result += " (information only)"
    lines = [
        f"# Backtest: {run.setup} (Jev {run.jev_mode})",
        "",
        f"**Strategy:** {run.strategy_version} · **Result:** {result}",
        "",
    ]
    if run.setup == "combined":
        lines += ["**Information only:** a combined run does not change pass or fail.", ""]
    jev: dict[str, Any] | None = run.metrics.get("jev")
    if jev is not None and run.jev_mode == "filter":
        lines += [
            (
                "**Information only — cannot change the v1 result.** The spec 02 Jev-off v1 "
                f"result stands; this run applies the Jev filter at theta {jev['theta_block']} "
                "from `data/jev_filter_v1.yaml` (spec 03)."
            ),
            "",
        ]
    if jev is not None and run.setup == "sentiment":
        lines += [f"**Sentiment status:** {_sentiment_status(run, jev)}", "", _period(run), ""]
    if run.strategy_version != "v1":
        lines += [
            (
                f"**POST-HOC** ({run.strategy_version}): cannot overturn a v1 result on its own. "
                "A PASS only means the variant did not fail on the past; nothing goes live from it."
            ),
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


def _sentiment_status(run: BacktestRun, jev: dict[str, Any]) -> str:
    if run.passed:
        return "PASS: the Sentiment setup may go live (spec 03)"
    if jev["information_only"]:
        return (
            "information only (fewer than 30 trades): live messages may mention positive "
            "documents, but no Sentiment entries are sent (spec 03)"
        )
    return "FAIL: no Sentiment entries are sent (spec 03)"


def _period(run: BacktestRun) -> str:
    return (
        f"**Period (spec 03):** {run.start_date.isoformat()} to {run.end_date.isoformat()}; the "
        f"halves split at the calendar midpoint (H1 entries to {run.metrics['h1_end']}, H2 "
        f"from {run.metrics['h2_start']})."
    )


def _jev(m: dict[str, Any]) -> list[str]:
    jev: dict[str, Any] | None = m.get("jev")
    if jev is None:
        return []
    builds = ", ".join(f"{build} ({count})" for build, count in jev["builds"].items())
    theta = "off" if jev["theta_block"] is None else jev["theta_block"]
    oos = jev["out_of_sample"]
    return [
        "## Jev readings",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Model requested | {jev['model_requested']} |",
        f"| Question set | {jev['question_set']} |",
        f"| Readings used | {jev['readings']} |",
        f"| Resolved builds | {builds or 'none'} |",
        f"| Filter theta_block | {theta} |",
        "",
        f"## Trades signalled on or after {jev['out_of_sample_since']} (out of sample for Jev)",
        "",
        (
            f"Trades {oos['trades']} · win rate {_pct(oos['win_rate'])} · "
            f"mean R {oos['mean_r']:.3f} · median R {oos['median_r']:.3f}"
        ),
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


def _cash_vehicle(m: dict[str, Any]) -> list[str]:
    vehicle: dict[str, Any] | None = m.get("cash_vehicle")
    if vehicle is None:
        return []
    symbol = vehicle["symbol"]
    sensitivity = vehicle["sensitivity"]
    return [
        f"## Idle cash in {symbol}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Switching cost per side | {_cost(vehicle['cost_per_side'])} |",
        f"| Average share of equity in {symbol} | {_pct(vehicle['share_vehicle'])} |",
        f"| Average share of equity in stocks | {_pct(vehicle['share_stocks'])} |",
        f"| Average share of equity in cash | {_pct(vehicle['share_cash'])} |",
        (
            f"| {symbol} switches (buys / sells) | {vehicle['switches']} "
            f"({vehicle['buys']} / {vehicle['sells']}) |"
        ),
        f"| Total switching cost (equity units) | {vehicle['switch_cost']:.2f} |",
        "",
        (
            f"Exposure above counts sessions holding a stock; {symbol} is not counted. Total "
            f"return, Sharpe, and drawdown are of total equity, {symbol} included."
        ),
        "",
        (
            f"**Sensitivity (information only): {symbol} switching at "
            f"{_cost(sensitivity['cost_per_side'])}** — total return "
            f"{_pct(sensitivity['total_return'])}, CAGR {_pct(sensitivity['cagr'])}, Sharpe "
            f"{sensitivity['sharpe']:.2f}, max drawdown {_pct(sensitivity['max_drawdown'])}. "
            f"Only the {_cost(vehicle['cost_per_side'])} run is stored and judged."
        ),
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
        *([f"- {caveat}" for caveat in JEV_CAVEATS] if "jev" in m else []),
        *([f"- {caveat}" for caveat in QQQ_CAVEATS] if "cash_vehicle" in m else []),
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


def _open_at_end(run: BacktestRun) -> list[str]:
    rows: list[dict[str, Any]] = run.trade_log.get("open_at_end", [])
    lines = ["## Positions open at the end", ""]
    if not rows:
        return [*lines, "None."]
    return [
        *lines,
        (
            "Still held at the last close, marked at that close before exit costs. They are "
            "excluded from the trade stats (trades, win rate, mean and median R, halves); "
            "the equity metrics include them."
        ),
        "",
        "| Symbol | Setup | Entry | Entry price | Last close | Unrealized P&L |",
        "| --- | --- | --- | --- | --- | --- |",
        *[
            f"| {row['symbol']} | {row['setup']} | {row['entry_date']} "
            f"| {row['entry_price']:.2f} | {row['last_close']:.2f} | {row['unrealized_pnl']:.2f} |"
            for row in rows
        ],
    ]


def render_report(run: BacktestRun) -> str:
    sections = [
        _header(run),
        _pass_bar(run),
        _metrics(run.metrics),
        _jev(run.metrics),
        _benchmarks(run.metrics),
        _cash_vehicle(run.metrics),
        _skips_and_caveats(run.metrics),
        _trades(run),
        _open_at_end(run),
    ]
    return "\n\n".join("\n".join(section) for section in sections if section) + "\n"
