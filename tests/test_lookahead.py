from datetime import UTC, date, datetime
from decimal import Decimal

from sqlmodel import Session

from signalbench.backtest.run import run_backtest_for_ticker
from signalbench.db.models import EventType, Price, RawDocument, Signal, Ticker


def test_extracted_in_2026_trades_asof_2022(session: Session) -> None:
    ticker = Ticker(symbol="AAPL", company_name="Apple Inc.", active=True)
    session.add(ticker)
    session.commit()
    session.refresh(ticker)
    doc = RawDocument(
        source="sec_edgar",
        external_id="old-8k",
        doc_type="eight_k",
        raw_text="beat",
        published_at=datetime(2022, 6, 1, tzinfo=UTC),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    session.add(
        Signal(
            document_id=doc.id,
            ticker_id=ticker.id,
            model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
            prompt_version="v1",
            sentiment=0.9,
            event_type=EventType.earnings,
            confidence=0.9,
            rationale="beat",
            extracted_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 1),
            open=Decimal(10),
            high=Decimal(10),
            low=Decimal(10),
            close=Decimal(10),
            adj_close=Decimal(10),
            volume=1,
        )
    )
    session.add(
        Price(
            ticker_id=ticker.id,
            date=date(2022, 6, 8),
            open=Decimal(11),
            high=Decimal(11),
            low=Decimal(11),
            close=Decimal(11),
            adj_close=Decimal(11),
            volume=1,
        )
    )
    session.commit()
    trades = run_backtest_for_ticker(
        session,
        ticker_id=ticker.id,
        sentiment_threshold=0.5,
        holding_days=1,
        model_version="deepseek-ai/DeepSeek-V4-Flash-0731",
        prompt_version="v1",
    )
    assert trades[0].entry_date == date(2022, 6, 1)
    assert trades[0].entry_date.year != 2026
