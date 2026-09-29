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
    Numeric,
    Text,
    UniqueConstraint,
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


class PaperPortfolio(SQLModel, table=True):
    """One pre-registered forward paper portfolio (spec 07). Created by `paper start`."""

    __tablename__ = "paper_portfolios"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    config_path: str  # repo-relative, e.g. data/strategy_v1.yaml
    config_sha256: str  # of the config at start; a config that no longer matches is refused
    setup: str  # pullback | breakout | combined
    started_on: date  # the first session stepped
    state: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))  # a SimState
    last_session: date | None = None  # updated with `state`, once per stepped session
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(UTCDateTime(), nullable=False),
    )
    start_git_sha: str | None = None  # HEAD of the code that ran `paper start`


class PaperEvent(SQLModel, table=True):
    """What happened to a paper portfolio in one session (spec 07). Append-only."""

    __tablename__ = "paper_events"

    id: int | None = Field(default=None, primary_key=True)
    portfolio_id: int = Field(foreign_key="paper_portfolios.id", ondelete="RESTRICT", index=True)
    session: date
    kind: str  # order_exit | order_entry | fill_exit | fill_entry | skip | exit_deferred | ...
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    recorded_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    catch_up: bool  # recorded after the next session's open, so recorded_at proves nothing


class PaperEquity(SQLModel, table=True):
    """A paper portfolio at one session's close (spec 07): one row per portfolio and session."""

    __tablename__ = "paper_equity"

    portfolio_id: int = Field(
        foreign_key="paper_portfolios.id", ondelete="RESTRICT", primary_key=True
    )
    session: date = Field(primary_key=True)
    equity: float
    cash: float
    vehicle_value: float
    open_positions: int
    catch_up: bool


class PaperRun(SQLModel, table=True):
    """One `signalbench paper run` (spec 07)."""

    __tablename__ = "paper_runs"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(UTCDateTime(), nullable=True)
    )
    status: str  # running | ok | failed
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    sessions_stepped: int = 0
    target_session: date | None = None
    git_sha: str | None = None  # HEAD of the code that ran (None when git itself failed)
    git_dirty: bool | None = None  # tracked code had uncommitted changes: the run was refused
