import json
from datetime import date
from pathlib import Path

from pytest import approx

from signalbench.backtest.benchmarks import BenchmarkStats
from signalbench.backtest.metrics import run_metrics
from signalbench.backtest.passbar import evaluate_pass_bar, passes
from signalbench.backtest.report import (
    CAVEATS,
    metrics_payload,
    pass_bar_payload,
    render_report,
    report_path,
    trade_log_payload,
)
from signalbench.backtest.simulator import simulate
from signalbench.db.models import BacktestRun
from signalbench.strategy.readings import NullReadingsView
from strategy_helpers import (
    load_test_config,
    make_market,
    pullback_closes,
    series,
    trend_bars,
    weekdays,
)

CONFIG = load_test_config().with_setups(("pullback",))
DAYS = weekdays(date(2023, 1, 2), 300)


def _run(setup: str = "pullback", version: str = "v1", end: int = 290) -> BacktestRun:
    closes = pullback_closes(len(DAYS))
    closes[252:] = [147.0] * (len(DAYS) - 252)
    market = make_market({"AAA": series(DAYS, closes)}, trend_bars(DAYS, 300.0, 0.5), DAYS, CONFIG)
    result = simulate(market, NullReadingsView(), CONFIG, DAYS[240], DAYS[end])
    metrics = run_metrics(result, CONFIG.backtest)
    benchmarks = [
        BenchmarkStats("QQQ buy-and-hold", 0.2, 0.1, 1.5, 0.05),
        BenchmarkStats("Survivor benchmark (equal weight)", 0.3, 0.12, 1.1, 0.08),
    ]
    bar = evaluate_pass_bar(metrics, 1.5, CONFIG.backtest)
    return BacktestRun(
        strategy_version=version,
        config_sha256="c" * 64,
        git_sha="abc123",
        setup=setup,
        jev_mode="off",
        start_date=result.start,
        end_date=result.end,
        data_fingerprint="d" * 64,
        metrics=metrics_payload(metrics, benchmarks, CONFIG.backtest),
        pass_bar=pass_bar_payload(bar),
        passed=passes(bar),
        trade_log=trade_log_payload(result),
    )


def test_payloads_are_plain_json() -> None:
    run = _run()
    for payload in (run.metrics, run.pass_bar, run.trade_log):
        assert json.loads(json.dumps(payload)) == payload
    assert run.trade_log["trades"][0]["signal_date"] == DAYS[251].isoformat()
    assert run.metrics["recent_since"] == "2026-09-15"
    assert run.pass_bar["trades"] == {"value": 1.0, "threshold": 30.0, "passed": False}


def test_report_has_provenance_pass_bar_benchmarks_caveats_and_trades() -> None:
    text = render_report(_run())
    assert text.startswith("# Backtest: pullback (Jev off)\n")
    assert "**Strategy:** v1 · **Result:** FAIL" in text
    for field in ("config_sha256 | `" + "c" * 64, "git_sha | `abc123`", "data_fingerprint | `"):
        assert field in text
    assert "| 1 | Trades | 1.000 | >= 30.000 | no |" in text
    assert "| 4 | Sharpe vs QQQ buy-and-hold |" in text
    assert "| QQQ buy-and-hold | 20.0% | 10.0% | 1.50 | 5.0% |" in text
    assert "| Survivor benchmark (equal weight) |" in text
    for caveat in CAVEATS:
        assert f"- {caveat}" in text
    assert "| 1 | AAA | pullback | " + DAYS[251].isoformat() in text
    assert "POST-HOC" not in text and "Information only" not in text


def test_combined_and_post_hoc_labels() -> None:
    assert "**Information only:**" in render_report(_run(setup="combined"))
    assert "**POST-HOC** (v2)" in render_report(_run(version="v2"))


def test_report_path() -> None:
    path = report_path(Path("reports/backtests"), date(2026, 9, 24), "breakout", "off")
    assert path == Path("reports/backtests/2026-09-24-breakout-off.md")


def test_passed_run_says_pass() -> None:
    run = _run()
    run.passed = True
    assert "**Result:** PASS" in render_report(run)


def test_positions_open_at_the_end_are_listed_apart_from_the_trades() -> None:
    # Signal on 251 (close 146, sized at equity / 3 on it), entry at the 252 open of 147
    # (x 1.002 cost); the run ends on 256, before the time exit, with closes of 147.
    run = _run(end=256)
    assert run.trade_log["trades"] == []
    [row] = run.trade_log["open_at_end"]
    fill = 147.0 * 1.002
    units = 100.0 / 3 / 146.0
    assert (row["symbol"], row["setup"], row["entry_date"]) == ("AAA", "pullback", DAYS[252].isoformat())
    assert row["last_close"] == approx(147.0)
    assert row["unrealized_pnl"] == approx(units * (147.0 - fill))
    text = render_report(run)
    assert "## Positions open at the end" in text
    assert "excluded from the trade stats" in text
    assert (
        f"| AAA | pullback | {DAYS[252].isoformat()} | {fill:.2f} | 147.00 "
        f"| {units * (147.0 - fill):.2f} |"
    ) in text


def test_no_open_positions_says_none() -> None:
    text = render_report(_run())
    assert "## Positions open at the end\n\nNone." in text
