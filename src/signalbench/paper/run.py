"""`crypto paper start` and the nightly `crypto paper run` (spec 11).

Each night: check the pre-registered rules, fetch Kraken's finished candles, replay the paper
period with the backtest's own step(), check that every saved day comes out exactly as saved,
append the new days in one transaction, and message the owner. Any failure saves no paper
rows, is recorded on the run row, and sends a PAPER ALERT.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import yaml
from sqlmodel import Session, col, select

from signalbench.backtest.crypto_runner import (
    CRYPTO_SYMBOLS,
    check_crypto_config,
    check_crypto_series,
    crypto_market,
    last_complete_day,
    load_crypto_bars,
)
from signalbench.db.models import PaperPortfolio, PaperRun
from signalbench.ingest.kraken import (
    crypto_ticker,
    find_crypto_ticker,
    ingest_kraken_bars,
)
from signalbench.ingest.prices import DailyBar
from signalbench.live.messenger import Messenger
from signalbench.market.calendar import HISTORY_START, DailySessions
from signalbench.paper.messages import alert_text, day_texts
from signalbench.paper.paper_file import PaperError, PaperFile, load_paper_file
from signalbench.paper.replay import ReplayDay, replay
from signalbench.paper.store import (
    append_days,
    find_portfolio,
    first_difference,
    load_saved,
)
from signalbench.paper.summary import paper_summary, summary_text
from signalbench.strategy.config import (
    ConfigError,
    StrategyConfig,
    load_strategy_config,
)

PAPER_LOCK = 2026_1011_11  # an arbitrary bigint that names the paper run's advisory lock
Fetch = Callable[[str, date], list[DailyBar]]  # (Kraken pair, today in UTC) -> finished candles


class ReplayMismatch(PaperError):
    """A saved day no longer comes out of the replay as saved."""


@dataclass(frozen=True)
class PaperRules:
    paper: PaperFile
    config: StrategyConfig  # only the paper file's setup fires
    sha: str  # the strategy file's SHA-256


@dataclass(frozen=True)
class PaperOutcome:
    status: str  # locked | ok | failed | mismatch
    run_id: int | None = None
    days_added: int = 0
    error: str | None = None


@dataclass(frozen=True)
class _Night:
    rules: PaperRules
    portfolio: PaperPortfolio
    new: list[ReplayDay]


def check_rules(repo: Path, paper_path: Path, committed: Callable[[Path], bool]) -> PaperRules:
    """The paper file and its strategy are committed and unchanged, and the strategy is the
    one the paper file names (its SHA-256)."""
    if not committed(paper_path):
        raise PaperError(
            f"{paper_path.name} must be committed, unchanged (spec 11 pre-registration)."
        )
    paper = load_paper_file(paper_path)
    strategy_path = repo / paper.strategy
    if not committed(strategy_path):
        raise PaperError(f"{paper.strategy} must be committed, unchanged.")
    try:
        config, sha = load_strategy_config(strategy_path)
    except (OSError, ConfigError, yaml.YAMLError) as error:
        raise PaperError(f"{paper.strategy}: {error}") from None
    if sha != paper.strategy_sha256:
        raise PaperError(
            f"{paper.strategy} has SHA-256 {sha}, not the {paper.strategy_sha256} in "
            f"{paper_path.name}: the strategy changed."
        )
    check_crypto_config(config)
    return PaperRules(paper, config.with_setups((paper.setup,)), sha)


def start_paper(
    session: Session,
    *,
    repo: Path,
    paper_path: Path,
    now: datetime,
    fetch: Fetch,
    committed: Callable[[Path], bool],
) -> PaperPortfolio:
    """Create the paper file's portfolio and load Kraken's recent daily candles. Refuses a
    second portfolio of the same name and a start day that has already closed."""
    rules = check_rules(repo, paper_path, committed)
    paper = rules.paper
    if find_portfolio(session, paper.name) is not None:
        raise PaperError(f"{paper.name} already exists; a new idea is a new paper file.")
    last = last_complete_day(now)
    if last >= paper.start:
        raise PaperError(
            f"{paper.start} has already closed (the last complete UTC day is {last}): "
            "pre-register a later start in a new paper file."
        )
    today = now.astimezone(UTC).date()
    for pair in CRYPTO_SYMBOLS:
        ingest_kraken_bars(session, crypto_ticker(session, pair), fetch(pair, today), HISTORY_START)
    portfolio = PaperPortfolio(
        name=paper.name, paper_file=paper_path.name, strategy_sha256=rules.sha,
        start=paper.start, created_at=now,
    )
    session.add(portfolio)
    session.commit()
    session.refresh(portfolio)
    return portfolio


def run_paper(
    session: Session,
    *,
    lock: AbstractContextManager[bool],
    repo: Path,
    paper_path: Path,
    clock: Callable[[], datetime],
    fetch: Fetch,
    committed: Callable[[Path], bool],
    git_sha: str,
    messenger: Messenger,
    echo: Callable[[str], None],
) -> PaperOutcome:
    """The nightly job. `lock` yields False when another run holds it: nothing is done.
    `clock` must return timezone-aware times."""
    with lock as held:
        if not held:
            echo("Another paper run holds the lock; nothing to do.")
            return PaperOutcome("locked")
        now = clock()
        run = PaperRun(started_at=now, status="running", git_sha=git_sha)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = _id(run.id)
        try:
            night = _night(session, run_id, repo, paper_path, now, fetch, committed)
        except Exception as error:  # noqa: BLE001  # every failure is recorded and alerted
            session.rollback()
            status = "mismatch" if isinstance(error, ReplayMismatch) else "failed"
            text = _error_text(error)
            sent = _send(messenger, alert_text(text), echo)
            _finish(session, run_id, clock(), status=status, error=text, messages_failed=int(not sent))
            echo(f"ERROR: {text}")
            return PaperOutcome(status, run_id, error=text)
        failed = 0
        try:
            config = night.rules.config
            texts = [
                text
                for day in night.new
                for text in day_texts(config.version, day, config.cost_per_side)
            ]
            for text in texts:
                failed += int(not _send(messenger, text, echo))
            summary_sent = False
            if not _summary_sent_this_week(session, now):
                summary = paper_summary(
                    session, night.portfolio, night.rules.paper, config.start_equity, now
                )
                summary_sent = _send(messenger, summary_text(night.rules.paper, summary), echo)
                failed += int(not summary_sent)
        except Exception as error:  # noqa: BLE001  # the days are saved; still finish and alert
            session.rollback()
            text = _error_text(error)
            failed += int(not _send(messenger, alert_text(text), echo))
            _finish(
                session, run_id, clock(), status="failed", error=text,
                days_added=len(night.new), messages_failed=failed,
            )
            echo(f"ERROR: {text}")
            return PaperOutcome("failed", run_id, len(night.new), text)
        _finish(
            session, run_id, clock(), status="ok", days_added=len(night.new),
            messages_failed=failed, summary_sent=summary_sent,
        )
        return PaperOutcome("ok", run_id, len(night.new))


def _night(
    session: Session,
    run_id: int,
    repo: Path,
    paper_path: Path,
    now: datetime,
    fetch: Fetch,
    committed: Callable[[Path], bool],
) -> _Night:
    rules = check_rules(repo, paper_path, committed)
    paper = rules.paper
    portfolio = find_portfolio(session, paper.name)
    if portfolio is None:
        raise PaperError(f"No portfolio {paper.name}: run `signalbench crypto paper start` first.")
    if portfolio.strategy_sha256 != rules.sha or portfolio.start != paper.start:
        raise PaperError(
            f"{paper_path.name} no longer matches the portfolio {paper.name} was started with."
        )
    today = now.astimezone(UTC).date()
    for pair in CRYPTO_SYMBOLS:
        ticker = find_crypto_ticker(session, pair)
        if ticker is None:
            raise PaperError(f"No {pair} ticker: run `signalbench crypto paper start` first.")
        ingest_kraken_bars(session, ticker, fetch(pair, today), HISTORY_START)
    last = last_complete_day(now)
    if last < paper.start:
        return _Night(rules, portfolio, [])
    bars = load_crypto_bars(session, last)
    check_crypto_series(bars, paper.start, last)
    market = crypto_market(bars, rules.config, DailySessions(), paper.start, last)
    days = replay(market, rules.config, paper.start, last)
    portfolio_id = _id(portfolio.id)
    saved = load_saved(session, portfolio_id)
    difference = first_difference(saved, days)
    if difference is not None:
        raise ReplayMismatch(f"replay mismatch on {difference}")
    new = days[len(saved):]
    append_days(session, portfolio_id=portfolio_id, run_id=run_id, days=new, now=now)
    session.commit()
    return _Night(rules, portfolio, new)


def _summary_sent_this_week(session: Session, now: datetime) -> bool:
    """A run since Monday 00:00 UTC already sent the weekly summary."""
    utc = now.astimezone(UTC)
    monday = datetime.combine(utc.date() - timedelta(days=utc.weekday()), time(0, 0), tzinfo=UTC)
    found = session.exec(
        select(PaperRun.id).where(
            col(PaperRun.summary_sent).is_(True), col(PaperRun.started_at) >= monday
        )
    ).first()
    return found is not None


def _send(messenger: Messenger, text: str, echo: Callable[[str], None]) -> bool:
    try:
        messenger.send(text)
    except Exception as error:  # noqa: BLE001  # Telegram down: the days are saved; the run counts it
        echo(f"Telegram send failed: {type(error).__name__}: {error}")
        return False
    return True


def _error_text(error: Exception) -> str:
    return str(error) if isinstance(error, PaperError) else f"{type(error).__name__}: {error}"


def _finish(
    session: Session,
    run_id: int,
    finished_at: datetime,
    *,
    status: str,
    error: str | None = None,
    days_added: int = 0,
    messages_failed: int = 0,
    summary_sent: bool = False,
) -> None:
    run = session.get(PaperRun, run_id)
    if run is None:
        raise RuntimeError(f"paper run {run_id} disappeared")
    run.finished_at = finished_at
    run.status = status
    run.error = error
    run.days_added = days_added
    run.messages_failed = messages_failed
    run.summary_sent = summary_sent
    session.add(run)
    session.commit()


def _id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("a saved row has no id")
    return value
