from datetime import UTC, date, datetime
from decimal import Decimal

from sqlmodel import Session

from signalbench.backtest.run import persist_run
from signalbench.db.models import EventType, Price, RawDocument, Signal, Ticker


def test_persist_run_ignores_ipo_era_prices(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="window-8k",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2024, 8, 21, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="m",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
        )
    )
    for day, px in [
        (date(1980, 12, 12), "1"),
        (date(2024, 8, 21), "100"),
        (date(2024, 8, 22), "101"),
        (date(2024, 8, 23), "102"),
    ]:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal(px),
                high=Decimal(px),
                low=Decimal(px),
                close=Decimal(px),
                adj_close=Decimal(px),
                volume=1,
            )
        )
    session.commit()
    run = persist_run(
        session,
        ticker_id=ticker.id,
        params={"sentiment_threshold": 0.5, "holding_days": 1, "name": "window"},
        model_version="m",
        prompt_version="v1",
    )
    assert run.start_date == date(2024, 8, 21)
    assert run.end_date == date(2024, 8, 23)
    assert run.benchmark_return == 0.02
    assert run.trade_log != []


def test_persist_run_ignores_signals_before_price_window(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="old-8k",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2015, 1, 15, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="m",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
        )
    )
    for day, px in [
        (date(1980, 12, 12), "1"),
        (date(2024, 8, 21), "100"),
        (date(2024, 8, 22), "101"),
        (date(2024, 8, 23), "102"),
    ]:
        session.add(
            Price(
                ticker_id=ticker.id,
                date=day,
                open=Decimal(px),
                high=Decimal(px),
                low=Decimal(px),
                close=Decimal(px),
                adj_close=Decimal(px),
                volume=1,
            )
        )
    session.commit()
    run = persist_run(
        session,
        ticker_id=ticker.id,
        params={"sentiment_threshold": 0.5, "holding_days": 1, "name": "old-signal"},
        model_version="m",
        prompt_version="v1",
    )
    assert run.start_date == date(2024, 8, 21)
    assert run.trade_log == []
    assert run.total_return == 0.0
