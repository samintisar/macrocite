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


class EventType(str, Enum):
    earnings = "earnings"
    guidance = "guidance"
    leadership = "leadership"
    legal = "legal"
    product = "product"
    macro = "macro"
    other = "other"


class Signal(SQLModel, table=True):
    __tablename__ = "signals"
    # Table-model __init__ skips Pydantic; validate_assignment enforces Field ge/le.
    model_config = SQLModel.model_config.copy()
    model_config["validate_assignment"] = True
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ticker_id",
            "model_version",
            "prompt_version",
            name="uq_signals_doc_ticker_model_prompt",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    document_id: uuid.UUID = Field(foreign_key="raw_documents.id", ondelete="CASCADE")
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    model_version: str
    prompt_version: str
    sentiment: float = Field(ge=-1.0, le=1.0)
    event_type: EventType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str | None = None
    raw_llm_response: dict[str, object] | None = Field(default=None, sa_column=Column(JSON))
    extracted_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    model_version: str
    prompt_version: str
    git_commit_sha: str | None = None
    label_set_git_sha: str | None = None
    n_examples: int
    sentiment_accuracy: float
    event_type_metrics: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    confidence_calibration: dict[str, object] | None = Field(
        default=None, sa_column=Column(JSON)
    )
    passed_ci_gate: bool
    run_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BacktestConfig(SQLModel, table=True):
    __tablename__ = "backtest_configs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str
    strategy_type: str
    params: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BacktestRun(SQLModel, table=True):
    __tablename__ = "backtest_runs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    config_id: uuid.UUID = Field(foreign_key="backtest_configs.id", ondelete="RESTRICT")
    ticker_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    start_date: date
    end_date: date
    model_version: str
    prompt_version: str
    signal_set_fingerprint: str
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    win_rate: float | None = None
    total_return: float | None = None
    benchmark_return: float | None = None
    trade_log: list[object] | None = Field(default=None, sa_column=Column(JSON))
    run_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    def __init__(self, **data: Any) -> None:
        if "signal_set_fingerprint" not in data:
            raise TypeError("signal_set_fingerprint is required")
        super().__init__(**data)
