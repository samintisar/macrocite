"""`signalbench scan` (spec 05, Evening scan): the live strategy's nightly job.

Eleven steps, recorded in the run's `scan_runs` row. A critical step that fails stops the
scan, marks the row failed with the step, and sends `⚠️ Scan failed at step N (<name>): ...`;
the next scheduled run retries. Steps 3 (earnings calendar) and 4 (splits) are not critical:
their failures are warnings in the summary, and a split problem holds only its symbol.

The target is the latest complete NYSE session (16:15 New York). Sessions missed since the last
successful scan are caught up first, in order, for exits and stop raises only (sent marked
late); entries come only from the target. A target already scanned is not scanned again without
`force`, and every row is unique (a signal per session and symbol, a raise per position and
session), so a forced rescan never sends a row twice. One scan runs at a time (`lock`).
"""

from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Literal, TypeVar

from sqlalchemy import Engine
from sqlmodel import Session, col, func, select

from signalbench.backtest.provenance import code_version
from signalbench.backtest.runner import last_complete_session
from signalbench.db.models import (
    CorporateAction,
    Price,
    ScanRun,
    StopUpdateRow,
    Ticker,
    TickerKind,
    TradeSignal,
)
from signalbench.ingest.cdr import CdrEntry
from signalbench.live.book import (
    MARKET_CLOSE,
    NEW_YORK,
    ZERO,
    LedgerError,
    SplitKind,
    q4,
)
from signalbench.live.heartbeat import heartbeat_problem
from signalbench.live.ledger import Ledger
from signalbench.live.messages import failure_message
from signalbench.live.messenger import Messenger
from signalbench.live.outbox import send_unsent
from signalbench.live.review import SessionReview, live_market, review_session
from signalbench.live.scaleup import ScaleUpResult
from signalbench.live.sizing import CdrSize, entry_window, size_cdr
from signalbench.live.start import LiveRefusedError, verify_live_config
from signalbench.live.summary import (
    BacktestR,
    Tonight,
    backtest_r,
    evening_summary,
    pause_review,
)
from signalbench.live.why import why_line
from signalbench.market.calendar import Sessions
from signalbench.paper.splits import SplitFetcher
from signalbench.paper.start import uncommitted_code
from signalbench.strategy.config import StrategyConfig
from signalbench.strategy.decision import EntryOrder
from signalbench.strategy.market_view import MarketView

STEPS = {
    1: "database check",
    2: "prices",
    3: "earnings calendar",
    4: "splits",
    5: "catch-up",
    6: "tonight",
    7: "sizing",
    8: "why",
    9: "send",
    10: "expiry and scale-up",
    11: "summary",
}
LIVE_SCAN_LOCK = 2026_0930_05  # an arbitrary bigint that names the scan's advisory lock
ABANDONED_AFTER = timedelta(hours=2)  # a `running` row this old was killed or crashed
ABANDONED = "abandoned (killed or crashed)"
MARKET_OPEN = time(9, 30)  # New York: a run after the next session's open is a late run
ScanStatus = Literal["locked", "ok", "nothing", "failed"]
T = TypeVar("T")


class StepError(Exception):
    """A critical step failed: the scan stops and the run is marked failed."""

    def __init__(self, step: int, message: str) -> None:
        super().__init__(message)
        self.step = step
        self.message = message


@dataclass(frozen=True)
class ScanOutcome:
    status: ScanStatus  # nothing: the target was already scanned, so nothing was sent
    run_id: int | None = None
    as_of: date | None = None
    error: str | None = None
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Sized:
    entry: EntryOrder
    size: CdrSize
    cdr: Ticker
    why: str = ""


def _first_line(error: BaseException) -> str:
    lines = str(error).strip().splitlines()
    return f"{type(error).__name__}: {lines[0] if lines else ''}"


def scale_up_text(result: ScaleUpResult) -> str:
    """The scale-up result for the summary (spec 04, spec 05)."""
    if not result.active:
        return f"Scale-up check: {result.closed_managed} of 10 managed trades closed."
    if result.passed:
        return "All 4 checks passed. You can add C$900 when ready; record it with /deposit 900."
    failed = []
    if not result.checks["take_rate"]:
        failed.append(f"take rate {result.take_rate or ZERO:.2f} < 0.80")
    if not result.checks["slippage"]:
        failed.append(f"mean slippage {(result.mean_slippage or ZERO) * 100:.2f}% > 0.5%")
    if not result.checks["exit_misses"]:
        failed.append(f"{len(result.misses)} missed exit alerts")
    if not result.checks["limit_violations"]:
        failed.append(f"{len(result.violations)} buys above the 1% limit")
    return "Scale-up check: not yet (" + "; ".join(failed) + ")."


@contextmanager
def dry_run_session(engine: Engine) -> Iterator[Session]:
    """A session whose commits are savepoints inside one transaction that is rolled back at the
    end: `scan --dry-run` runs every step on the real data and writes nothing."""
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
                yield session
        finally:
            outer.rollback()


def run_scan(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    universe: list[CdrEntry],
    nyse: Sessions,
    cboe: Sessions,
    clock: Callable[[], datetime],
    ingest: Callable[[Session], list[str]],
    ingest_earnings: Callable[[Session, date], list[str]],
    splits: SplitFetcher,
    messenger: Messenger,
    echo: Callable[[str], None],
    as_of: date | None = None,
    force: bool = False,
) -> ScanOutcome:
    """The evening scan. `lock` yields False when another scan holds it: nothing is done.
    `ingest` refreshes prices and the liquidity flags and returns what failed; `ingest_earnings`
    refreshes the earnings calendar from a date; `splits` lists a symbol's splits after a date.
    `as_of` overrides the target (`--dry-run --as-of`); `force` rescans a scanned target.
    `clock` must return timezone-aware times."""
    with lock as held:
        if not held:
            echo("Another scan holds the lock; nothing to do.")
            return ScanOutcome("locked")
        now = clock()
        _abandon_stale_runs(session, now)
        target = as_of if as_of is not None else last_complete_session(nyse, now)
        run = ScanRun(as_of=target, started_at=now, status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        scan = _Scan(session, run, repo, universe, nyse, cboe, clock, messenger, echo)
        try:
            return scan.run(ingest, ingest_earnings, splits, force)
        except StepError as failure:
            session.rollback()
            text = failure_message(failure.step, STEPS[failure.step], failure.message)
            scan.finish("failed", failure.step, failure.message)
            try:
                messenger.send(text)
            except Exception as error:  # noqa: BLE001  # Telegram down: the script's toast shows
                echo(f"could not send the failure message ({_first_line(error)})")
            echo(f"ERROR: {text.removeprefix('⚠️ ')}")
            return ScanOutcome("failed", run.id, target, failure.message, scan.counts())


def _abandon_stale_runs(session: Session, now: datetime) -> None:
    """A scan killed or crashed mid-way leaves its row `running`. The lock is held here, so an
    old one is not in progress: it counts as failed."""
    rows = session.exec(select(ScanRun).where(ScanRun.status == "running")).all()
    for row in rows:
        if now - row.started_at > ABANDONED_AFTER:
            row.status = "failed"
            row.error = ABANDONED
            session.add(row)
    session.commit()


class _Scan:
    def __init__(
        self,
        session: Session,
        run: ScanRun,
        repo: Path,
        universe: list[CdrEntry],
        nyse: Sessions,
        cboe: Sessions,
        clock: Callable[[], datetime],
        messenger: Messenger,
        echo: Callable[[str], None],
    ) -> None:
        self.session = session
        self.run_row = run
        self.target = run.as_of
        self.repo = repo
        self.universe = universe
        self.nyse = nyse
        self.cboe = cboe
        self.clock = clock
        self.now = clock()
        self.messenger = messenger
        self.echo = echo
        self.ledger = Ledger(session, calendar=nyse, today=self.now.astimezone(NEW_YORK).date())
        self.warnings: list[str] = []
        self.tally: Counter[str] = Counter()
        self.skips: Counter[str] = Counter()
        self.hold: set[str] = set()
        self.pause_texts: list[str] = []
        self.caught_up: list[date] = []
        self.backtest: BacktestR | None = None

    # --- Bookkeeping -----------------------------------------------------------------------

    def counts(self) -> dict[str, int]:
        return dict(self.tally)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self.echo(f"warning: {text}")

    def finish(self, status: str, step: int | None = None, error: str | None = None) -> None:
        run = self.run_row
        run.status = status
        run.finished_at = self.clock()
        run.failed_step = step
        run.error = error
        run.warnings = list(self.warnings)
        run.counts = self.counts()
        self.session.add(run)
        self.session.commit()

    def step(self, number: int, work: Callable[[], T]) -> T:
        try:
            return work()
        except StepError:
            raise
        except Exception as error:  # any error in a critical step fails the scan
            raise StepError(number, _first_line(error)) from error

    # --- The scan --------------------------------------------------------------------------

    def run(
        self,
        ingest: Callable[[Session], list[str]],
        ingest_earnings: Callable[[Session, date], list[str]],
        splits: SplitFetcher,
        force: bool,
    ) -> ScanOutcome:
        config = self.step(1, self.check)
        last_ok = self.session.exec(
            select(func.max(ScanRun.as_of)).where(ScanRun.status == "ok")
        ).one()
        if last_ok is not None and last_ok >= self.target and not force:
            self.echo(f"{self.target} was already scanned; nothing to send.")
            self.check_heartbeat()
            self.finish("ok")
            return ScanOutcome("nothing", self.run_row.id, self.target, None, self.counts())
        sessions = [self.target]
        if last_ok is not None and last_ok < self.target:
            sessions = self.nyse.sessions_between(last_ok + timedelta(days=1), self.target)
        self.step(2, lambda: self.prices(ingest, config))
        self.earnings(ingest_earnings, last_ok)
        self.splits(splits)
        market = self.step(
            5, lambda: live_market(self.session, self.universe, config, self.nyse, self.target)
        )
        for day in sessions[:-1]:
            self.step(5, partial(self.review, day, market, config, True))
            self.caught_up.append(day)
        tonight = self.step(6, lambda: self.review(self.target, market, config, self.late_run()))
        sized = self.step(7, lambda: self.size(tonight, config))
        explained = self.step(8, lambda: [self.explain(s, market, config) for s in sized])
        self.step(9, lambda: self.send(explained))
        scale_up = self.step(10, self.expire_and_scale_up)
        self.step(11, lambda: self.summary(market, config, scale_up))
        self.finish("ok")
        return ScanOutcome("ok", self.run_row.id, self.target, None, self.counts())

    def check(self) -> StrategyConfig:
        """Step 1: the live_config row, the config's sha256, and a clean tree."""
        try:
            row, config = verify_live_config(self.session, self.repo)
        except LiveRefusedError as error:
            raise StepError(1, str(error)) from None
        version = code_version(self.repo)
        run = self.run_row
        run.git_sha, run.git_dirty, run.config_sha256 = version.sha, version.dirty, row.config_sha256
        self.session.add(run)
        self.session.commit()
        if version.dirty:
            raise StepError(1, uncommitted_code(version))
        self.backtest = backtest_r(self.session, row.config_sha256)
        return config

    def late_run(self) -> bool:
        """After the next NYSE session's open: the target's orders were due at that open."""
        after = self.nyse.next_sessions(self.target, 1)[0]
        late = self.now >= datetime.combine(after, MARKET_OPEN, tzinfo=NEW_YORK)
        if late:
            self.warn(f"late run: this scan ran after the {after} open, so its orders are late")
        return late

    def _us_ticker(self, symbol: str) -> Ticker | None:
        return self.session.exec(
            select(Ticker).where(Ticker.symbol == symbol, Ticker.kind == TickerKind.us_stock)
        ).first()

    def _held_and_pending(self) -> set[str]:
        """The US symbols of every open position and sent signal."""
        symbols = {s.us_symbol for s in self._sent_signals()}
        for episode in self.ledger.open_episodes():
            cdr = self.session.get(Ticker, episode.cdr_ticker_id)
            us = None if cdr is None or cdr.us_ticker_id is None else self.session.get(
                Ticker, cdr.us_ticker_id
            )
            if us is not None:
                symbols.add(us.symbol)
        return symbols

    def _sent_signals(self) -> list[TradeSignal]:
        return list(
            self.session.exec(
                select(TradeSignal).where(TradeSignal.status == "sent").order_by(col(TradeSignal.id))
            ).all()
        )

    def prices(self, ingest: Callable[[Session], list[str]], config: StrategyConfig) -> None:
        """Step 2: ingest prices; the target needs a bar for QQQ and every held or pending
        symbol. Any other universe name without one is just not tradable tonight."""
        failed = ingest(self.session)
        if failed:
            self.warn(f"prices: {len(failed)} failed ({', '.join(failed)}); stored prices used")
        wanted = sorted({config.regime_symbol} | self._held_and_pending())
        missing = []
        for symbol in wanted:
            ticker = self.session.exec(select(Ticker).where(Ticker.symbol == symbol)).first()
            bar = None if ticker is None else self.session.exec(
                select(Price.id).where(Price.ticker_id == ticker.id, Price.date == self.target)
            ).first()
            if bar is None:
                missing.append(symbol)
        if missing:
            raise StepError(
                2, f"no {self.target} bar for {', '.join(missing)}; the next run retries"
            )

    def earnings(
        self, ingest_earnings: Callable[[Session, date], list[str]], last_ok: date | None
    ) -> None:
        """Step 3, not critical: the calendar from the last successful scan's as_of on, so the
        catch-up sessions keep their dates. On failure the stored dates are used."""
        since = self.target if last_ok is None else last_ok
        try:
            failed = ingest_earnings(self.session, since)
        except Exception as error:  # noqa: BLE001  # the stored dates still serve
            self.session.rollback()
            self.warn(f"earnings calendar FAILED ({_first_line(error)}); the stored dates were used")
            return
        if failed:
            self.warn(
                f"earnings calendar: {len(failed)} failed ({', '.join(failed)}); the stored dates "
                "were used for them"
            )

    def splits(self, fetch: SplitFetcher) -> None:
        """Step 4, not critical: record new splits of held and pending names, then the scale
        check. A failed lookup, a split that cannot be recorded, or prices no recorded split
        explains hold that symbol's decisions tonight, with a warning."""
        try:
            self._record_splits(fetch)
            self._scale_check()
        except Exception as error:  # noqa: BLE001  # hold everything held rather than stop
            self.session.rollback()
            held = self._held_and_pending()
            self.hold |= held
            self.warn(
                f"split check FAILED ({_first_line(error)}); no decisions tonight for "
                f"{', '.join(sorted(held)) or 'nothing'}"
            )

    def _lookups(self) -> dict[tuple[str, SplitKind, str], tuple[date, str]]:
        """(price symbol, kind, ledger symbol) -> (since, US symbol): the CDR of every open
        position since its opening buy, the US stock of every managed one since its signal, and
        both for every sent signal since its as_of."""
        found: dict[tuple[str, SplitKind, str], tuple[date, str]] = {}

        def add(ticker: Ticker, kind: SplitKind, since: date, us_symbol: str) -> None:
            key = (ticker.price_symbol or ticker.symbol, kind, ticker.symbol)
            earlier = found.get(key)
            found[key] = (since if earlier is None else min(since, earlier[0]), us_symbol)

        for episode in self.ledger.open_episodes():
            cdr = self.session.get(Ticker, episode.cdr_ticker_id)
            assert cdr is not None and cdr.us_ticker_id is not None
            us = self.session.get(Ticker, cdr.us_ticker_id)
            assert us is not None
            add(cdr, "cdr_split", episode.opening.trade_date, us.symbol)
            if episode.signal_id is not None:
                add(us, "us_split", self.ledger.signal(episode.signal_id).as_of, us.symbol)
        for signal in self._sent_signals():
            cdr = self.session.get(Ticker, signal.cdr_ticker_id)
            us = self._us_ticker(signal.us_symbol)
            assert cdr is not None and us is not None
            add(cdr, "cdr_split", signal.as_of, us.symbol)
            add(us, "us_split", signal.as_of, us.symbol)
        return found

    def _recorded(self, kind: SplitKind, symbol: str, ex_date: date) -> bool:
        query = select(CorporateAction.id).where(
            CorporateAction.kind == kind, CorporateAction.ex_date == ex_date,
            col(CorporateAction.voided).is_(False),
        )
        if kind == "us_split":
            query = query.where(CorporateAction.us_symbol == symbol)
        else:
            cdr = self.session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
            query = query.where(CorporateAction.cdr_ticker_id == cdr.id)
        return self.session.exec(query).first() is not None

    def _record_splits(self, fetch: SplitFetcher) -> None:
        for (price_symbol, kind, symbol), (since, us_symbol) in sorted(self._lookups().items()):
            try:
                found = fetch(price_symbol, since)
            except Exception as error:  # noqa: BLE001  # a lookup that failed is not "no split"
                self.hold.add(us_symbol)
                self.warn(
                    f"{us_symbol}: the split lookup for {price_symbol} failed "
                    f"({_first_line(error)}); no decisions for it tonight"
                )
                continue
            for split in sorted(found, key=lambda s: s.ex_date):
                if split.ex_date > self.target or self._recorded(kind, symbol, split.ex_date):
                    continue
                try:
                    self.ledger.record_split(
                        kind=kind, symbol=symbol, ex_date=split.ex_date,
                        ratio=Decimal(repr(split.ratio)), source="yfinance",
                    )
                except LedgerError as error:
                    self.hold.add(us_symbol)
                    self.warn(f"{us_symbol}: the {split.ex_date} split was not recorded ({error})")
                    continue
                self.tally["splits"] += 1

    def _scale_check(self) -> None:
        signal_ids = [s.id for s in self._sent_signals() if s.id is not None]
        signal_ids += [
            e.signal_id for e in self.ledger.open_episodes() if e.signal_id is not None
        ]
        for signal_id in signal_ids:
            problem = self.ledger.scale_check(signal_id)
            if problem is not None:
                symbol = self.ledger.signal(signal_id).us_symbol
                self.hold.add(symbol)
                self.warn(
                    f"{symbol}: prices moved in a way no recorded split explains; check it by "
                    f"hand ({problem})"
                )

    def review(
        self, day: date, market: MarketView, config: StrategyConfig, late: bool
    ) -> SessionReview:
        """Steps 5 and 6: the equity snapshot and the pause, then decide() for `day`, and the
        exit alerts and raises it calls for (a raise is recorded now, so later sessions use it)."""
        _, paused_now = self.ledger.record_equity(
            day, pause_drawdown=Decimal(repr(config.pause_drawdown))
        )
        if paused_now:
            self.pause_texts.append(pause_review(self.ledger, self.session, self.backtest))
        result = review_session(self.ledger, market, config, day, hold=self.hold)
        for call in result.exits:
            if self.ledger.open_exit_alert(call.signal_id) is None:
                self.ledger.record_exit_alert(
                    signal_id=call.signal_id, as_of=day, reason=call.reason, late=late
                )
                self.tally["exits"] += 1
        for up in result.raises:
            taken = self.session.exec(
                select(StopUpdateRow.id).where(
                    StopUpdateRow.signal_id == up.signal_id, StopUpdateRow.session == day,
                    StopUpdateRow.reason == "trail",
                )
            ).first()
            if taken is None:
                self.ledger.record_stop_update(
                    signal_id=up.signal_id, session=day, new_us_stop=up.new_us_stop, late=late
                )
                self.tally["raises"] += 1
        return result

    def size(self, tonight: SessionReview, config: StrategyConfig) -> list[Sized]:
        """Step 7: each entry on the CDR, in rank order, from the cash no pending signal holds."""
        self.skips.update(skip.reason for skip in tonight.decision.skips)
        snapshot = self.ledger.equity(self.target)
        cash = self.ledger.cash(self.target) - sum(
            (s.suggested_units * s.cdr_signal_close for s in self.ledger.pending_signals(self.target)),
            ZERO,
        )
        cdrs = {entry.us_symbol: entry.cdr_symbol for entry in self.universe}
        sized: list[Sized] = []
        for entry in tonight.decision.entries:
            recorded = self.session.exec(
                select(TradeSignal.id).where(
                    TradeSignal.as_of == self.target, TradeSignal.us_symbol == entry.symbol,
                    TradeSignal.setup == "breakout",
                )
            ).first()
            if recorded is not None:
                self.skips["already_sent"] += 1
                continue
            cdr = self.session.exec(
                select(Ticker).where(Ticker.symbol == cdrs.get(entry.symbol, ""))
            ).first()
            close = None if cdr is None or not cdr.active else self.session.exec(
                select(col(Price.close))
                .where(Price.ticker_id == cdr.id, col(Price.date) <= self.target)
                .order_by(col(Price.date).desc())
            ).first()
            if cdr is None or close is None:
                self.skips["no_cdr_price"] += 1
                continue
            size = size_cdr(
                us_signal_close=q4(Decimal(repr(entry.signal_close))),
                us_stop=q4(Decimal(repr(entry.stop))), cdr_close=close,
                equity=snapshot.equity, uncommitted_cash=cash,
                risk_pct=Decimal(repr(config.risk_pct)), max_positions=config.max_positions,
            )
            if size is None:
                self.skips["no_cash"] += 1
                continue
            cash -= size.cost
            sized.append(Sized(entry, size, cdr))
        return sized

    def explain(self, sized: Sized, market: MarketView, config: StrategyConfig) -> Sized:
        """Step 8: the template Why line, from the Snapshot that fired the rule."""
        snapshot = market.at(self.target).snapshot(sized.entry.symbol)
        assert snapshot is not None
        return Sized(sized.entry, sized.size, sized.cdr, why_line(snapshot, config.breakout))

    def send(self, explained: list[Sized]) -> None:
        """Step 9: write tonight's signals, then send every unsent row and any pause review."""
        window = entry_window(self.target, self.nyse, self.cboe)
        for item in explained:
            self.ledger.record_signal(
                as_of=self.target, us_symbol=item.entry.symbol, cdr_symbol=item.cdr.symbol,
                us_signal_close=q4(Decimal(repr(item.entry.signal_close))),
                us_stop=q4(Decimal(repr(item.entry.stop))), cdr_signal_close=item.size.cdr_close,
                suggested_units=item.size.units, order_type=item.size.order_type,
                risk_amount_cad=item.size.risk, explanation=item.why,
                expires_at=window.expires_at,
            )
            self.tally["signals"] += 1
        self.tally["sent"] += send_unsent(
            self.session, self.ledger, self.messenger, nyse=self.nyse, cboe=self.cboe,
            now=self.now,
        )
        for text in self.pause_texts:
            self.messenger.send(text)
            self.tally["sent"] += 1

    def expire_and_scale_up(self) -> str | None:
        """Step 10: signals whose entry session has closed expire; the scale-up check runs after
        each close of a managed position. Returns its line for the summary when it ran."""
        close = datetime.combine(self.target, MARKET_CLOSE, tzinfo=NEW_YORK)
        for signal in self._sent_signals():
            if signal.expires_at <= close and signal.id is not None:
                self.ledger.mark_signal(signal.id, "expired")
                self.tally["expired"] += 1
        closed = sum(1 for trade in self.ledger.closed_trades() if trade.episode.managed)
        history = self.ledger.risk_state().scale_up_history
        checked = int(history[-1]["closed_managed"]) if history else 0
        if closed <= checked:
            return None
        result = self.ledger.scale_up_check()
        self.ledger.record_scale_up(result)
        return scale_up_text(result)

    def check_heartbeat(self) -> None:
        problem = heartbeat_problem(self.session, self.now)
        if problem is not None:
            self.warnings.append(problem)
            self.echo(f"BOT STALE: {problem}")

    def summary(self, market: MarketView, config: StrategyConfig, scale_up: str | None) -> None:
        """Step 11: the evening summary."""
        view = market.at(self.target)
        allowed = view.regime_on()
        regime = (
            f"{config.regime_symbol} {'above' if allowed else 'below'} its {config.regime_sma}-"
            f"session average: new entries {'allowed' if allowed else 'off'}"
        )
        self.check_heartbeat()
        tonight = Tonight(
            as_of=self.target, regime=regime, signals=self.tally["signals"], skips=self.skips,
            exits=self.tally["exits"], raises=self.tally["raises"],
            caught_up=tuple(self.caught_up), warnings=tuple(self.warnings), scale_up=scale_up,
        )
        self.messenger.send(evening_summary(self.ledger, self.session, tonight))
        self.tally["sent"] += 1
