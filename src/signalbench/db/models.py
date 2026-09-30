import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class DocType(str, Enum):
    news = "news"
    eight_k = "eight_k"
    ten_k = "ten_k"
    ten_q = "ten_q"


class TickerKind(str, Enum):
    us_stock = "us_stock"
    cdr = "cdr"
    benchmark = "benchmark"


class Ticker(SQLModel, table=True):
    __tablename__ = "tickers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    symbol: str = Field(unique=True, index=True)
    company_name: str
    sector: str | None = None
    active: bool = Field(default=True)
    added_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    kind: TickerKind = Field(default=TickerKind.us_stock)
    price_symbol: str | None = None
    us_ticker_id: uuid.UUID | None = Field(
        default=None,
        foreign_key="tickers.id",
        ondelete="RESTRICT",
    )


class RawDocument(SQLModel, table=True):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_raw_documents_source_external_id"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    source: str
    external_id: str
    doc_type: DocType
    url: str | None = None
    title: str | None = None
    raw_text: str = Field(sa_column=Column(Text, nullable=False))
    published_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    acceptance_at: datetime | None = Field(
        default=None,
        sa_column=Column(UTCDateTime(), nullable=True),
    )
    items: str | None = None
    form: str | None = None
    text: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    ingested_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class DocumentTicker(SQLModel, table=True):
    __tablename__ = "document_tickers"

    document_id: uuid.UUID = Field(
        foreign_key="raw_documents.id",
        primary_key=True,
        ondelete="CASCADE",
    )
    ticker_id: uuid.UUID = Field(
        foreign_key="tickers.id",
        primary_key=True,
        ondelete="RESTRICT",
    )


class Price(SQLModel, table=True):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_prices_ticker_date"),)

    id: int | None = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    date: date
    open: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    adj_close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    volume: int = Field(sa_column=Column(BigInteger, nullable=False))


class EarningsEvent(SQLModel, table=True):
    __tablename__ = "earnings_events"
    __table_args__ = (
        UniqueConstraint(
            "ticker_id",
            "event_date",
            "source",
            name="uq_earnings_events_ticker_date_source",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    event_date: date
    source: str


class BacktestRun(SQLModel, table=True):
    """One stored spec 02 backtest run. JSON payloads are written by backtest/runner.py."""

    __tablename__ = "backtest_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    strategy_version: str
    config_sha256: str
    git_sha: str
    setup: str  # pullback | breakout | sentiment | combined
    jev_mode: str  # off | filter
    start_date: date
    end_date: date
    data_fingerprint: str
    metrics: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    pass_bar: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    passed: bool
    trade_log: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    run_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )


class JevReading(SQLModel, table=True):
    """One Jev reading of one document for one ticker (spec 03). Written by `jev backfill`."""

    __tablename__ = "jev_readings"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_requested",
            "question_set",
            name="uq_jev_readings_document_ticker_model_questions",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    document_id: uuid.UUID = Field(
        foreign_key="raw_documents.id", ondelete="CASCADE", index=True
    )
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    model_requested: str
    model_resolved: str
    question_set: str
    response_id: str
    p_negative: float
    p_neutral: float
    p_positive: float
    event_type: str
    p_routine: float
    answers: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    input_tokens: int
    cost_usd: float
    latency_ms: int
    read_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )


def _money(precision: int = 12, scale: int = 4) -> Any:
    """A NOT NULL Numeric column for a Decimal field (spec 04: money is CAD, stored as Numeric)."""
    return Field(sa_column=Column(Numeric(precision, scale), nullable=False))


def _telegram_id() -> Any:
    """Telegram message ids are 64-bit integers; None until the message is sent (spec 05)."""
    return Field(default=None, sa_column=Column(BigInteger, nullable=True))


def _created_at() -> Any:
    return Field(default_factory=utcnow, sa_column=Column(UTCDateTime(), nullable=False))


class LiveConfig(SQLModel, table=True):
    """The one live strategy config (spec 04), frozen by `signalbench live start`. One row."""

    __tablename__ = "live_config"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    config_path: str  # repo-relative: data/strategy_v2-none-cash.yaml
    config_sha256: str  # the scan refuses a config file whose sha256 differs (spec 05)
    started_on: date  # the New York date `live start` ran
    start_git_sha: str  # HEAD of the code that ran `live start`
    created_at: datetime = _created_at()


class CashMovement(SQLModel, table=True):
    """A deposit (+) or withdrawal (-) in CAD (spec 04). Never edited."""

    __tablename__ = "cash_movements"

    id: int | None = Field(default=None, primary_key=True)
    amount_cad: Decimal = _money(12, 2)
    occurred_on: date
    note: str = Field(sa_column=Column(Text, nullable=False))


class TradeSignal(SQLModel, table=True):
    """One entry signal sent to the owner (spec 04, written by the evening scan of spec 05)."""

    __tablename__ = "trade_signals"
    __table_args__ = (
        UniqueConstraint("as_of", "us_symbol", "setup", name="uq_trade_signals_as_of_symbol_setup"),
    )

    id: int | None = Field(default=None, primary_key=True)
    as_of: date  # the signal session
    us_symbol: str
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    setup: str = "breakout"  # always breakout live; the column stays for clarity
    us_signal_close: Decimal = _money()  # USD, on the scale of `as_of`
    us_stop: Decimal = _money()
    stop_pct: Decimal = _money(12, 8)  # (us_signal_close - us_stop) / us_signal_close
    cdr_signal_close: Decimal = _money()  # CAD
    cdr_stop: Decimal = _money()  # cdr_signal_close x (1 - stop_pct)
    suggested_units: Decimal = _money(18, 6)
    order_type: str  # limit | market
    risk_amount_cad: Decimal = _money()
    explanation: str = Field(sa_column=Column(Text, nullable=False))  # the template Why line
    status: str = "sent"  # sent | taken | skipped | expired | withdrawn
    skip_reason: str | None = None  # disagree | no_time | price_moved | wide_spread | other
    telegram_message_id: int | None = _telegram_id()
    expires_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    created_at: datetime = _created_at()


class ExitAlert(SQLModel, table=True):
    """A stop or earnings exit for a managed position (spec 04). One `sent` at a time."""

    __tablename__ = "exit_alerts"

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="trade_signals.id", ondelete="RESTRICT", index=True)
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    as_of: date  # the session whose close triggered it
    reason: str  # stop | earnings
    late: bool = False  # sent by a catch-up run
    status: str = "sent"  # sent | done | ignored
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class CorporateAction(SQLModel, table=True):
    """A US or CDR stock split (spec 04). Append-only: a wrong row is voided, never edited."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        Index(
            "uq_corporate_actions_us_split", "us_symbol", "ex_date", unique=True,
            sqlite_where=text("kind = 'us_split' AND NOT voided"),
            postgresql_where=text("kind = 'us_split' AND NOT voided"),
        ),
        Index(
            "uq_corporate_actions_cdr_split", "cdr_ticker_id", "ex_date", unique=True,
            sqlite_where=text("kind = 'cdr_split' AND NOT voided"),
            postgresql_where=text("kind = 'cdr_split' AND NOT voided"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # us_split | cdr_split
    us_symbol: str | None = None  # us_split only
    cdr_ticker_id: uuid.UUID | None = Field(
        default=None, foreign_key="tickers.id", ondelete="RESTRICT"
    )  # cdr_split only
    ex_date: date  # fills dated on or after it are in post-split units
    ratio: Decimal = _money(12, 6)  # new units per old unit: 2 for a 2-for-1 split
    source: str  # yfinance | owner
    voided: bool = False
    void_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class StopUpdateRow(SQLModel, table=True):
    """A managed position's stop change (spec 04): a `trail` raise or a `split` rescale.

    Append-only. `corporate_action_id` links a `split` row to the split it applies or undoes
    (an addition to the spec's columns: voiding a split finds the rows to undo through it).
    """

    __tablename__ = "stop_updates"
    __table_args__ = (
        Index(
            "uq_stop_updates_trail_signal_session", "signal_id", "session", unique=True,
            sqlite_where=text("reason = 'trail'"),
            postgresql_where=text("reason = 'trail'"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="trade_signals.id", ondelete="RESTRICT", index=True)
    session: date  # trail: the session whose close raised it; split: the ex-date
    reason: str  # trail | split
    old_us_stop: Decimal = _money()
    new_us_stop: Decimal = _money()
    old_cdr_stop: Decimal = _money()
    new_cdr_stop: Decimal = _money()
    late: bool = False
    corporate_action_id: int | None = Field(
        default=None, foreign_key="corporate_actions.id", ondelete="RESTRICT"
    )
    telegram_message_id: int | None = _telegram_id()
    created_at: datetime = _created_at()


class Fill(SQLModel, table=True):
    """What the owner did on Wealthsimple (spec 04). Never deleted: a mistake is voided."""

    __tablename__ = "fills"

    id: int | None = Field(default=None, primary_key=True)
    cdr_ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT", index=True)
    side: str  # buy | sell
    quantity: Decimal = _money(18, 6)  # CDR units, post-split from the ex-date on
    price_cad: Decimal = _money()
    fee_cad: Decimal = Field(default=Decimal(0), sa_column=Column(Numeric(12, 4), nullable=False))
    trade_date: date
    signal_id: int | None = Field(default=None, foreign_key="trade_signals.id", ondelete="RESTRICT")
    exit_alert_id: int | None = Field(
        default=None, foreign_key="exit_alerts.id", ondelete="RESTRICT"
    )
    voided: bool = False
    void_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    forced: bool = False  # a buy beyond the cash on hand, recorded with force=True (logged)
    created_at: datetime = _created_at()


class EquitySnapshot(SQLModel, table=True):
    """Equity at one session's close (spec 04), written nightly by the scan."""

    __tablename__ = "equity_snapshots"
    __table_args__ = (PrimaryKeyConstraint("date"),)  # a `date` default would shadow the type

    date: date  # the session
    cash: Decimal = _money(14, 4)
    positions_value: Decimal = _money(14, 4)
    equity: Decimal = _money(14, 4)
    peak: Decimal = _money(14, 4)  # max equity since risk_state.peak_reset_on


class LiveRiskState(SQLModel, table=True):
    """The pause state (spec 04). One row. Only /resume clears a pause."""

    __tablename__ = "risk_state"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    paused: bool = False
    paused_at: date | None = None  # the session whose close paused new entries
    paused_reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    resumed_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    peak_reset_on: date | None = None  # set by /resume: the peak counts from this date
    # Each scale-up check result and its inputs, oldest first (spec 04, "stored for the record")
    scale_up_history: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )


class ScanRun(SQLModel, table=True):
    """One `signalbench scan` (spec 05). `running` until it ends; a scan killed or crashed
    leaves it `running`, and the next scan marks it failed once it is two hours old."""

    __tablename__ = "scan_runs"

    id: int | None = Field(default=None, primary_key=True)
    as_of: date  # the target session
    started_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    status: str  # running | ok | failed
    failed_step: int | None = None  # 1-11 (spec 05, Evening scan)
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    warnings: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    counts: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    git_sha: str | None = None  # HEAD of the code that ran (None when git itself failed)
    git_dirty: bool | None = None  # tracked code had uncommitted changes: the scan was refused
    config_sha256: str | None = None  # the live config's, once step 1 has checked it


class ScanHold(SQLModel, table=True):
    """A session whose exits and stop raises the scan held back for a managed position's US
    symbol, because its scale check failed (spec 05, step 4). The first scan after the hold
    clears reviews it, marked late, and sets `reviewed`."""

    __tablename__ = "scan_holds"
    __table_args__ = (
        UniqueConstraint("us_symbol", "session", name="uq_scan_holds_symbol_session"),
    )

    id: int | None = Field(default=None, primary_key=True)
    us_symbol: str
    session: date  # the held session
    reviewed: bool = False
    created_at: datetime = _created_at()


class BotUpdate(SQLModel, table=True):
    """A Telegram update whose command or reply the bot recorded (spec 05), written in the same
    transaction as the fill or cash movement: an update redelivered after a crash is refused."""

    __tablename__ = "bot_updates"

    update_id: int = Field(
        sa_column=Column(BigInteger, primary_key=True, autoincrement=False)
    )
    created_at: datetime = _created_at()


class BotHeartbeat(SQLModel, table=True):
    """The bot's last sign of life (spec 05): one row, rewritten at least every 10 minutes."""

    __tablename__ = "bot_heartbeat"

    id: int = Field(default=1, primary_key=True, sa_column_kwargs={"autoincrement": False})
    beat_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
