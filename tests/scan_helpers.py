"""A Breakout world for the scan tests (spec 05), on the weekday calendar (no holidays).

230 weekdays of stored bars. Every US stock is flat (high/low = close +/- 1, so ATR(14) is
exactly 2) on 1M shares, and QQQ rises all along (regime on). NVDA breaks out on day B:
101 on 2M shares, above the prior 20-session high of 100, so the signal close is US$101.00 and
the initial stop 101 - 2 x 2 = 97. It then rises by 1 a session to 104 on B+3 (the trailing
stop 104 - 3 x 2 = 98 > 97) and falls to 97.5 on B+4, below that raised stop. Every CDR trades
at a tenth of its US price on 100 units a day. The live config is the real v2-none-cash file,
committed in a throwaway repo and frozen by a live_config row.
"""

import shutil
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from live_helpers import add_benchmark, add_pair, add_prices, make_ledger, universe
from paper_helpers import evening, git
from signalbench.db.models import LiveConfig, Price, Ticker
from signalbench.ingest.cdr import CdrEntry
from signalbench.ingest.prices import Split
from signalbench.live.heartbeat import write_heartbeat
from signalbench.live.ledger import Ledger
from signalbench.live.messenger import FakeMessenger, Messenger
from signalbench.live.scan import ScanOutcome, run_scan
from signalbench.market.calendar import Sessions
from signalbench.strategy.config import load_strategy_config
from strategy_helpers import WeekdaySessions, weekdays

REPO = Path(__file__).resolve().parents[1]
LIVE = "data/strategy_v2-none-cash.yaml"
DAYS = weekdays(date(2025, 12, 1), 230)
B = 220  # the breakout session: 220 sessions of history before it
UNIVERSE: list[CdrEntry] = universe(
    ("AAPL", "ZAAP", "Information Technology"),
    ("NVDA", "ZNVD", "Information Technology"),
    ("XOM", "ZXOM", "Energy"),
)
NVDA = {B: 101.0, B + 1: 102.0, B + 2: 103.0, B + 3: 104.0, B + 4: 97.5}


def path(changes: dict[int, float], start: float = 100.0) -> list[float]:
    """Flat at `start`, then each change from its index on."""
    closes, close = [], start
    for index in range(len(DAYS)):
        close = changes.get(index, close)
        closes.append(close)
    return closes


def volumes(spikes: dict[int, int]) -> list[int]:
    return [spikes.get(index, 1_000_000) for index in range(len(DAYS))]


def make_repo(root: Path) -> Path:
    """A committed copy of the live config and one tracked source file."""
    (root / "data").mkdir(parents=True)
    shutil.copyfile(REPO / LIVE, root / LIVE)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    git(root, "init", "-q")
    git(root, "add", "data", "src")
    git(root, "commit", "-q", "-m", "live")
    return root


@dataclass
class World:
    session: Session
    repo: Path
    messenger: FakeMessenger = field(default_factory=FakeMessenger)
    splits: dict[str, list[Split]] = field(default_factory=dict)
    earnings_error: Exception | None = None
    bot_down: bool = False
    cboe: Sessions = field(default_factory=WeekdaySessions)
    echoed: list[str] = field(default_factory=list)

    def ledger(self, index: int) -> Ledger:
        return make_ledger(self.session, today=DAYS[index])

    def scan(
        self,
        index: int,
        *,
        messenger: Messenger | None = None,
        force: bool = False,
        held: bool = True,
        hour: int = 18,
    ) -> ScanOutcome:
        def earnings(_session: Session, _since: date) -> list[str]:
            if self.earnings_error is not None:
                raise self.earnings_error
            return []

        if not self.bot_down:  # the bot checked in five minutes ago
            write_heartbeat(self.session, evening(DAYS[index], hour) - timedelta(minutes=5))
        return run_scan(
            self.session, lock=nullcontext(held), repo=self.repo, universe=UNIVERSE,
            nyse=WeekdaySessions(), cboe=self.cboe,
            clock=lambda: evening(DAYS[index], hour), ingest=lambda _session: [],
            ingest_earnings=earnings, splits=lambda symbol, _since: self.splits.get(symbol, []),
            messenger=self.messenger if messenger is None else messenger,
            echo=self.echoed.append, force=force,
        )


def make_world(
    session: Session,
    root: Path,
    *,
    closes: dict[str, dict[int, float]] | None = None,
    spikes: dict[str, dict[int, int]] | None = None,
) -> World:
    """The stocks, their CDRs, QQQ, a C$100 deposit on the first day, and the frozen config."""
    closes = {"NVDA": NVDA} if closes is None else closes
    spikes = {"NVDA": {B: 2_000_000}} if spikes is None else spikes
    for entry in UNIVERSE:
        us, cdr = add_pair(session, entry.us_symbol, entry.cdr_symbol, entry.sector)
        us_closes = path(closes.get(entry.us_symbol, {}))
        add_prices(session, us, DAYS, us_closes, volumes=volumes(spikes.get(entry.us_symbol, {})))
        add_prices(session, cdr, DAYS, [round(c / 10, 4) for c in us_closes], volume=100)
    add_benchmark(session, DAYS, [300.0 + i for i in range(len(DAYS))])
    repo = make_repo(root)
    sha = load_strategy_config(repo / LIVE)[1]
    session.add(LiveConfig(config_path=LIVE, config_sha256=sha, started_on=DAYS[0],
                           start_git_sha=git(repo, "rev-parse", "HEAD")))
    session.commit()
    make_ledger(session, today=DAYS[0]).record_cash(Decimal("100.00"), DAYS[0], "deposit")
    return World(session, repo)


class Holiday(WeekdaySessions):
    """The weekday calendar with one day closed: a Cboe Canada holiday when NYSE is open."""

    def __init__(self, closed: date) -> None:
        self.closed = closed

    def sessions_between(self, start: date, end: date) -> list[date]:
        return [d for d in super().sessions_between(start, end) if d != self.closed]

    def next_sessions(self, day: date, count: int) -> list[date]:
        return [d for d in super().next_sessions(day, count + 1) if d != self.closed][:count]

    def is_session(self, day: date) -> bool:
        return day != self.closed and super().is_session(day)


def savepoint_engine() -> Engine:
    """In-memory SQLite that honours savepoints, as Postgres does for `dry_run_session`
    (pysqlite's own transaction handling does not)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _no_autobegin(dbapi_connection: object, _record: object) -> None:
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _begin(connection: object) -> None:
        connection.exec_driver_sql("BEGIN")  # type: ignore[attr-defined]

    SQLModel.metadata.create_all(engine)
    return engine


def rescale(session: Session, symbol: str, ratio: float) -> None:
    """The stored history after a `ratio`-for-1 split, as yfinance serves it."""
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    for row in session.exec(select(Price).where(Price.ticker_id == ticker.id)).all():
        factor = Decimal(repr(ratio))
        row.open, row.high, row.low = row.open / factor, row.high / factor, row.low / factor
        row.close, row.adj_close = row.close / factor, row.adj_close / factor
        session.add(row)
    session.commit()
