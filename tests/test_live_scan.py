"""Spec 05, Evening scan: the steps and their run row, failures, idempotency, the lock, splits,
and the dry run, on the Breakout world of scan_helpers (FakeMessenger, no network)."""

from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, col, select

from repo_helpers import evening, git
from scan_helpers import (
    DAYS,
    LIVE,
    NVDA,
    UNIVERSE,
    B,
    Holiday,
    World,
    make_world,
    rescale,
    savepoint_engine,
)
from signalbench.db.models import (
    CorporateAction,
    ExitAlert,
    LiveConfig,
    Price,
    ScanRun,
    StopUpdateRow,
    Ticker,
    TradeSignal,
)
from signalbench.ingest.prices import Split
from signalbench.live.messenger import (
    ConsoleMessenger,
    FakeMessenger,
    TelegramMessenger,
)
from signalbench.live.scan import ABANDONED, _Scan, dry_run_session, run_scan
from strategy_helpers import WeekdaySessions
from telegram_helpers import CHAT, TOKEN, Rejecting

ENTRY = (
    "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA\n"
    "Signal C$10.10 · Stop C$9.70 (−4.0%) · Trailing stop, no target, no time limit\n"
    "Size 3 units (~C$30.30) · risk C$1.20 · LIMIT C$10.20\n"
    "Skip if price > C$10.20, price ≤ the stop C$9.70, or bid/ask spread > 0.5%\n"
    "Why: Closed at a 20-session high (US$101.00) on 2.0× its 50-session average volume, above "
    "its 50-session average."
)


@pytest.fixture
def world(session: Session, tmp_path: Path) -> World:
    return make_world(session, tmp_path)


def _runs(session: Session) -> list[ScanRun]:
    return list(session.exec(select(ScanRun).order_by(col(ScanRun.id))).all())


def _bought(world: World) -> int:
    """The owner bought 3 ZNVD at C$10.45 the day after the signal."""
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.id is not None
    world.ledger(B + 1).record_fill(cdr_symbol="ZNVD", side="buy", quantity=Decimal(3),
                                    price_cad=Decimal("10.45"), trade_date=DAYS[B + 1],
                                    signal_id=signal.id)
    return signal.id


def test_a_breakout_is_sized_explained_recorded_and_sent(world: World) -> None:
    outcome = world.scan(B)
    assert (outcome.status, outcome.as_of, outcome.counts) == (
        "ok", DAYS[B], {"signals": 1, "sent": 2}
    )
    entry, summary = world.messenger.sent
    assert entry.text == ENTRY
    assert summary.text.splitlines()[:4] == [
        "📊 Evening summary · 2026-10-05 (Mon)",
        "QQQ above its 200-session average: new entries allowed",
        "Signals sent 1 · skipped: none · exits 0 · stop raises 0",
        "Positions: none",
    ]
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert (signal.us_signal_close, signal.us_stop, signal.cdr_signal_close, signal.cdr_stop) == (
        Decimal(101), Decimal(97), Decimal("10.10"), Decimal("9.70")
    )
    assert (signal.suggested_units, signal.order_type, signal.risk_amount_cad) == (
        Decimal(3), "limit", Decimal("1.20")
    )
    assert signal.explanation.startswith("Closed at a 20-session high (US$101.00)")
    assert signal.expires_at == evening(DAYS[B + 1], 16)  # the close of the next session
    assert signal.telegram_message_id == entry.message_id
    [run] = _runs(world.session)
    assert (run.status, run.as_of, run.failed_step, run.warnings, run.git_dirty) == (
        "ok", DAYS[B], None, [], False
    )
    assert run.config_sha256 is not None and len(run.git_sha or "") == 40


def test_a_scanned_target_sends_nothing_and_a_forced_rescan_never_resends_a_row(
    world: World,
) -> None:
    world.scan(B)
    again = world.scan(B, hour=19)
    assert (again.status, len(world.messenger.sent)) == ("nothing", 2)
    assert world.echoed[-1] == "2026-10-05 was already scanned; nothing to send."
    forced = world.scan(B, force=True, hour=20)
    assert forced.status == "ok"
    assert [m.text.split()[0] for m in world.messenger.sent[2:]] == ["📊"]  # the summary only
    assert "skipped: already_sent 1" in world.messenger.sent[-1].text
    assert [r.status for r in _runs(world.session)] == ["ok", "ok", "ok"]


def test_a_row_whose_send_failed_is_sent_by_the_next_run(world: World) -> None:
    down = FakeMessenger(fail=True)
    failed = world.scan(B, messenger=down)
    assert failed.status == "failed"
    unreachable = "ConnectionError: Telegram is unreachable (FakeMessenger)"
    assert world.echoed[-2:] == [
        f"could not send the failure message ({unreachable})",
        f"ERROR: Scan failed at step 9 (send): {unreachable}",
    ]
    [run] = _runs(world.session)
    assert (run.status, run.failed_step) == ("failed", 9)
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.telegram_message_id is None
    retried = world.scan(B, hour=19)
    assert retried.status == "ok"
    assert world.messenger.texts()[0] == ENTRY  # sent once, by the retry
    assert len(world.session.exec(select(TradeSignal)).all()) == 1


def test_a_critical_failure_sends_the_warning_and_records_the_step(world: World) -> None:
    qqq = world.session.exec(select(Ticker).where(Ticker.symbol == "QQQ")).one()
    bar = world.session.exec(
        select(Price).where(Price.ticker_id == qqq.id, Price.date == DAYS[B])
    ).one()
    world.session.delete(bar)
    world.session.commit()
    outcome = world.scan(B)
    assert outcome.status == "failed"
    assert world.messenger.texts() == [
        "⚠️ Scan failed at step 2 (prices): no 2026-10-05 bar for QQQ; the next run retries"
    ]
    [run] = _runs(world.session)
    assert (run.status, run.failed_step, run.error) == (
        "failed", 2, "no 2026-10-05 bar for QQQ; the next run retries"
    )


def test_a_calendar_ingest_failure_is_a_warning_and_the_stored_dates_are_used(
    world: World,
) -> None:
    world.earnings_error = ConnectionError("finnhub down")
    assert world.scan(B).status == "ok"
    warning = (
        "earnings calendar FAILED (ConnectionError: finnhub down); the stored dates were used"
    )
    assert f"⚠️ {warning}" in world.messenger.sent[-1].text.splitlines()
    assert _runs(world.session)[0].warnings == [warning]


def test_a_changed_config_a_missing_row_and_a_dirty_tree_are_refused(world: World) -> None:
    config = world.repo / LIVE
    original = config.read_bytes()
    config.write_bytes(original + b"# edited\n")
    assert world.scan(B).error is not None
    assert "changed: sha256" in world.messenger.texts()[-1]
    config.write_bytes(original)
    (world.repo / "src" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    world.scan(B)
    assert world.messenger.texts()[-1] == (
        "⚠️ Scan failed at step 1 (database check): uncommitted changes to tracked code or data "
        "(src/app.py); commit them or check out a clean tag, then rerun"
    )
    assert _runs(world.session)[-1].git_dirty is True


def test_a_missing_live_config_row_is_refused(session: Session, tmp_path: Path) -> None:
    world = make_world(session, tmp_path)
    row = session.get(LiveConfig, 1)
    session.delete(row)
    session.commit()
    world.scan(B)
    [failure] = world.messenger.texts()
    assert failure == (
        "⚠️ Scan failed at step 1 (database check): No live config. Run `signalbench live start` "
        "first."
    )


def test_a_second_scan_exits_while_the_lock_is_held(world: World) -> None:
    assert world.scan(B, held=False).status == "locked"
    assert (_runs(world.session), world.messenger.sent) == ([], [])


def test_a_run_left_running_for_two_hours_is_marked_abandoned(world: World) -> None:
    stale = ScanRun(as_of=DAYS[B - 1], started_at=evening(DAYS[B]) - timedelta(hours=3),
                    status="running")
    world.session.add(stale)
    world.session.commit()
    world.scan(B)
    first = _runs(world.session)[0]
    assert (first.status, first.error) == ("failed", ABANDONED)


def test_a_split_in_a_held_stock_writes_a_split_row_and_a_notice(world: World) -> None:
    world.scan(B)
    signal_id = _bought(world)
    world.scan(B + 1)
    rescale(world.session, "NVDA", 2.0)  # yfinance's history after a 2-for-1 split on B+2
    world.splits = {"NVDA": [Split(ex_date=DAYS[B + 2], ratio=2.0)]}
    outcome = world.scan(B + 2)
    assert (outcome.status, outcome.counts["splits"]) == ("ok", 1)
    assert "ℹ️ NVDA split 2-for-1 (ex-date 2026-10-07): stop US$97.00 → US$48.50 · no action" in (
        world.messenger.texts()
    )
    [row] = world.session.exec(select(StopUpdateRow).where(StopUpdateRow.reason == "split")).all()
    assert (row.signal_id, row.new_us_stop, row.new_cdr_stop) == (
        signal_id, Decimal("48.50"), Decimal("9.70")
    )


def test_an_unrecorded_split_holds_that_symbol_and_the_rest_of_the_scan_goes_on(
    world: World,
) -> None:
    world.scan(B)
    _bought(world)
    world.scan(B + 1)
    rescale(world.session, "NVDA", 2.0)  # a split yfinance did not list
    outcome = world.scan(B + 2)
    assert outcome.status == "ok"
    summary = world.messenger.sent[-1].text
    assert (
        "⚠️ NVDA: prices moved in a way no recorded split explains; check it by hand (the "
        "stored US close 50.5000 on 2026-10-05 is 0.5000x the signal's 101.0000 after recorded "
        "splits)"
    ) in summary.splitlines()
    assert "stop raises 0" in summary


def test_a_cboe_holiday_moves_the_entry_and_its_expiry(world: World) -> None:
    world.cboe = Holiday(DAYS[B + 1])  # Cboe Canada closed on the next NYSE session
    world.scan(B)
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.expires_at == evening(DAYS[B + 2], 16)
    assert world.messenger.texts()[0].splitlines()[4] == (
        "Cboe Canada is closed on Tue 06 Oct: place it on Wed 07 Oct."
    )


def test_the_bot_heartbeat_is_checked(world: World) -> None:
    world.bot_down = True
    world.scan(B)
    assert world.echoed[-1] == (
        "BOT STALE: the bot has never checked in: is `signalbench bot run` running?"
    )
    assert "⚠️ the bot has never checked in: is `signalbench bot run` running?" in (
        world.messenger.sent[-1].text.splitlines()
    )


def test_a_drawdown_pauses_new_entries_and_sends_the_review(world: World) -> None:
    world.scan(B - 1)  # the peak: C$100 on 2026-10-02
    world.ledger(B).record_fill(cdr_symbol="ZXOM", side="buy", quantity=Decimal(4),
                                price_cad=Decimal(15), trade_date=DAYS[B])  # worth C$40 at C$10
    before = len(world.messenger.sent)
    world.scan(B)
    review, summary = world.messenger.texts()[before:]  # no entry: the breakout is skipped
    assert review.splitlines()[0] == (
        "⏸ New entries are paused since the close of 2026-10-05: equity C$80.00 is below 0.85 "
        "x the peak C$100.00."
    )
    assert "skipped: paused 1" in summary
    assert "new entries PAUSED since 2026-10-05 (/resume)" in summary


def test_a_withdrawal_lowers_the_peak_too_so_it_never_pauses(world: World) -> None:
    world.scan(B - 1)  # the peak: C$100 on 2026-10-02
    world.ledger(B).record_cash(Decimal("-20.00"), DAYS[B], "withdrawal")
    world.scan(B)
    assert world.messenger.texts()[-2].startswith("🟢 BUY NVDA")  # no pause review
    assert "peak C$80.00 · drawdown 0.0% · new entries not paused" in world.messenger.texts()[-1]


def test_a_run_after_the_next_open_is_late_and_says_so(world: World) -> None:
    world.scan(B + 1, hour=8)  # before B+1 is complete, the target is still B; before its open
    [run] = _runs(world.session)
    assert (run.as_of, run.warnings) == (DAYS[B], [])
    world.scan(B + 1, hour=10, force=True)  # after the 09:30 open of B+1
    assert _runs(world.session)[-1].warnings == [
        "late run: this scan ran after the 2026-10-06 open, so its orders are late"
    ]


def test_a_dry_run_prints_every_message_and_writes_nothing(tmp_path: Path) -> None:
    engine = savepoint_engine()
    with Session(engine) as seed:
        world = make_world(seed, tmp_path)
    lines: list[str] = []
    with dry_run_session(engine) as session:
        outcome = run_scan(
            session, lock=nullcontext(True), repo=world.repo, universe=UNIVERSE,
            nyse=WeekdaySessions(), cboe=WeekdaySessions(), clock=lambda: evening(DAYS[B]),
            ingest=lambda _s: [], ingest_earnings=lambda _s, _d: [],
            splits=lambda _symbol, _since: [], messenger=ConsoleMessenger(lines.append),
            echo=lambda _line: None, as_of=DAYS[B], force=True,
        )
    assert outcome.status == "ok"
    assert lines[:3] == ["--- message 1 ---", ENTRY, "[✅ I bought] [⏭ Skip]"]
    assert lines[3] == "--- message 2 ---"
    assert lines[4].startswith("📊 Evening summary · 2026-10-05 (Mon)")
    assert len(lines) == 5
    with Session(engine) as after:
        assert after.exec(select(ScanRun)).all() == []
        assert after.exec(select(TradeSignal)).all() == []


def _set_bar(world: World, symbol: str, index: int, close: str, volume: int) -> None:
    ticker = world.session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    bar = world.session.exec(
        select(Price).where(Price.ticker_id == ticker.id, Price.date == DAYS[index])
    ).one()
    value = Decimal(close)
    bar.open, bar.high, bar.low, bar.close, bar.adj_close = (
        value, value + 1, value - 1, value, value
    )
    bar.volume = volume
    world.session.add(bar)
    world.session.commit()


def test_the_cdr_reference_is_the_us_close_at_the_last_traded_ratio_not_a_stale_close(
    world: World,
) -> None:
    _set_bar(world, "ZNVD", B, "9.50", 0)  # no trade on B: a stale close carried forward
    world.scan(B)
    [signal] = world.session.exec(select(TradeSignal)).all()
    assert signal.cdr_signal_close == Decimal("10.10")  # US$101 x 0.1, the ratio of B-1
    assert world.messenger.texts()[0] == ENTRY
    world.scan(B + 1)  # the scale check compares the same basis: the stale close is no split
    assert _runs(world.session)[-1].warnings == []


def test_a_cdr_that_never_traded_is_skipped_with_no_cdr_price(world: World) -> None:
    znvd = world.session.exec(select(Ticker).where(Ticker.symbol == "ZNVD")).one()
    for bar in world.session.exec(select(Price).where(Price.ticker_id == znvd.id)).all():
        bar.volume = 0
        world.session.add(bar)
    world.session.commit()
    world.scan(B)
    assert world.session.exec(select(TradeSignal)).all() == []
    assert "skipped: no_cdr_price 1" in world.messenger.texts()[-1]


def test_a_retry_counts_the_failed_runs_signals_toward_slots_and_committed_cash(
    session: Session, tmp_path: Path
) -> None:
    # XOM trades 3M shares a day, so it outranks NVDA once it breaks out.
    world = make_world(session, tmp_path, spikes={
        "NVDA": {B: 2_000_000}, "XOM": {i: 3_000_000 for i in range(len(DAYS))},
    })
    # A manual position of C$70 leaves C$30 of cash: room for one entry.
    world.ledger(B - 1).record_fill(cdr_symbol="ZAAP", side="buy", quantity=Decimal(7),
                                    price_cad=Decimal(10), trade_date=DAYS[B - 1])
    assert world.scan(B, messenger=FakeMessenger(fail=True)).status == "failed"
    [nvda] = world.session.exec(select(TradeSignal)).all()
    assert (nvda.us_symbol, nvda.telegram_message_id) == ("NVDA", None)
    # The retry's prices show an XOM breakout too (a late bar), on 2x its volume.
    _set_bar(world, "XOM", B, "101", 6_000_000)
    _set_bar(world, "ZXOM", B, "10.1", 100)
    assert world.scan(B, hour=20).status == "ok"
    assert [s.us_symbol for s in world.session.exec(select(TradeSignal)).all()] == ["NVDA"]
    entry, summary = world.messenger.sent
    assert entry.text.startswith("🟢 BUY NVDA")
    assert "skipped: already_sent 1, no_cash 1" in summary.text


def test_exit_alerts_go_out_before_sizing_so_a_later_failure_keeps_them(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.scan(B)
    _bought(world)
    for index in range(B + 1, B + 4):
        world.scan(index)
    before = len(world.messenger.sent)

    def broken(*_args: object) -> list[object]:
        raise RuntimeError("sizing broke")

    monkeypatch.setattr(_Scan, "size", broken)
    outcome = world.scan(B + 4)
    assert (outcome.status, _runs(world.session)[-1].failed_step) == ("failed", 7)
    alert, failure = world.messenger.texts()[before:]
    assert alert.startswith("🔴 SELL NVDA (CDR ZNVD) — stop hit")
    assert failure == "⚠️ Scan failed at step 7 (sizing): RuntimeError: sizing broke"
    monkeypatch.undo()
    assert world.scan(B + 4, hour=20).status == "ok"  # the retry does not send it again
    assert [t for t in world.messenger.texts() if t.startswith("🔴")] == [alert]


def test_a_run_for_a_scanned_target_still_sends_rows_left_unsent(world: World) -> None:
    world.scan(B)
    world.ledger(B).record_split(kind="cdr_split", symbol="ZNVD", ex_date=DAYS[B + 1],
                                 ratio=Decimal(2), source="owner")  # by hand, after the scan
    again = world.scan(B, hour=20)
    assert again.status == "nothing"
    assert world.messenger.texts()[-1].startswith("ℹ️ ZNVD split 2-for-1 (ex-date 2026-10-06)")
    assert world.scan(B, hour=21).counts == {}  # nothing left to send


def test_a_late_run_marks_its_entries_late_and_keeps_their_buttons(world: World) -> None:
    world.scan(B + 1, hour=10)  # the first run for B is after B+1's 09:30 open
    entry = world.messenger.sent[0]
    assert entry.text.splitlines()[0] == (
        "🟢 BUY NVDA (CDR ZNVD) — Breakout · NVDA (late — check the price before placing)"
    )
    assert entry.text.splitlines()[1:] == ENTRY.splitlines()[1:]
    assert [b.data for row in entry.buttons for b in row] == ["b:1", "s:1"]


def test_the_scan_runs_only_the_live_start_commit_or_a_live_tag(world: World) -> None:
    (world.repo / "src" / "app.py").write_text("VERSION = 2\n", encoding="utf-8")
    git(world.repo, "commit", "-q", "-am", "an update")
    refused = world.scan(B)
    assert refused.status == "failed"
    head = git(world.repo, "rev-parse", "HEAD")
    assert world.messenger.texts()[-1] == (
        f"⚠️ Scan failed at step 1 (database check): HEAD {head[:12]} is neither the live start "
        "commit nor a live-v* tag; check out the live tag (an update is a new live-v* tag), or "
        "pass --allow-any-commit for development"
    )
    assert world.scan(B, allow_any_commit=True).status == "ok"
    git(world.repo, "tag", "live-v2")
    assert world.scan(B, force=True, hour=20).status == "ok"


def test_a_failed_split_lookup_is_a_warning_not_a_hold(world: World) -> None:
    world.scan(B)
    _bought(world)
    for index in range(B + 1, B + 3):
        world.scan(index)
    world.split_error = ConnectionError("yfinance down")
    world.scan(B + 3)
    run = _runs(world.session)[-1]
    assert run.warnings == [
        f"NVDA: the split lookup for {symbol} failed (ConnectionError: yfinance down); the "
        "scale check still guards it"
        for symbol in ("NVDA", "ZNVD.NE")
    ]
    assert run.counts["raises"] == 1  # 104 - 6 = 98: the stop is still managed


def test_a_one_for_three_split_ratio_is_rounded_to_six_decimals(world: World) -> None:
    world.scan(B)
    _bought(world)
    world.scan(B + 1)
    rescale(world.session, "NVDA", 1 / 3)  # a 1-for-3 reverse split on B+2
    world.splits = {"NVDA": [Split(ex_date=DAYS[B + 2], ratio=1 / 3)]}
    outcome = world.scan(B + 2)
    assert (outcome.counts["splits"], _runs(world.session)[-1].warnings) == (1, [])
    action = world.session.exec(select(CorporateAction)).one()
    assert action.ratio == Decimal("0.333333")


def test_a_held_session_whose_close_hit_the_stop_still_exits_once_the_hold_clears(
    session: Session, tmp_path: Path
) -> None:
    world = make_world(session, tmp_path, closes={"NVDA": {**NVDA, B + 5: 104.0}})
    world.scan(B)
    _bought(world)
    for index in range(B + 1, B + 4):
        world.scan(index)  # the stop is raised to 98 at B+3's close
    # B+4: a 2-for-1 split that yfinance does not list yet. NVDA closes at 97.50 (48.75 after
    # the split), below the stop, but the scale check holds it.
    rescale(world.session, "NVDA", 2.0)
    held = world.scan(B + 4)
    assert "exits" not in held.counts
    assert session.exec(select(ExitAlert)).all() == []
    # B+5: the split is listed, the check passes, and NVDA is back above the stop (52 > 49).
    # The held session B+4 is reviewed first: its close hit the stop.
    world.splits = {"NVDA": [Split(ex_date=DAYS[B + 4], ratio=2.0)]}
    before = len(world.messenger.sent)
    outcome = world.scan(B + 5)
    assert outcome.counts["exits"] == 1
    alert = session.exec(select(ExitAlert)).one()
    assert (alert.as_of, alert.late, alert.reason) == (DAYS[B + 4], True, "stop")
    texts = world.messenger.texts()[before:]
    assert texts[0].startswith("🔴 SELL NVDA (CDR ZNVD) — stop hit (US close US$48.75 ≤ stop "
                               "US$49.00")
    assert (
        "⚠️ NVDA: the held session 2026-10-09 was reviewed now that its prices check out "
        "(exits and stop raises only, sent marked late)"
    ) in texts[-1].splitlines()
    assert world.scan(B + 6).counts.get("exits") is None  # reviewed once


def test_a_rejected_bot_token_is_redacted_from_the_run_row_and_the_output(world: World) -> None:
    messenger = TelegramMessenger(TOKEN, CHAT, request=Rejecting())
    outcome = world.scan(B, messenger=messenger)
    assert outcome.error == "InvalidToken: The token `***` was rejected by the server."
    [run] = _runs(world.session)
    assert run.error == outcome.error
    assert not [line for line in world.echoed if TOKEN in line]
    assert world.echoed[-1] == (
        "ERROR: Scan failed at step 9 (send): InvalidToken: The token `***` was rejected by the "
        "server."
    )
