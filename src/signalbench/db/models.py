import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import Column, DateTime, Numeric, Text, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
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
    sector: Optional[str] = None
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
    url: Optional[str] = None
    title: Optional[str] = None
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

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker_id: uuid.UUID = Field(foreign_key="tickers.id", ondelete="RESTRICT")
    date: date
    open: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    high: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    low: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    adj_close: Decimal = Field(sa_column=Column(Numeric(12, 4), nullable=False))
    volume: int
