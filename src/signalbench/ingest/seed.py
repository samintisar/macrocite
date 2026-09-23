import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from signalbench.db.models import Ticker, TickerKind
from signalbench.ingest.cdr import CdrEntry

BENCHMARKS: tuple[tuple[str, str], ...] = (
    ("QQQ", "Invesco QQQ Trust"),
    ("SPY", "SPDR S&P 500 ETF Trust"),
)


@dataclass(frozen=True)
class SeedResult:
    us_stocks: int
    cdrs: int
    benchmarks: int
    deactivated: list[str]


def seed_universe(session: Session, entries: list[CdrEntry]) -> SeedResult:
    by_symbol = {ticker.symbol: ticker for ticker in session.exec(select(Ticker)).all()}
    keep: set[str] = set()
    for entry in entries:
        us = _upsert(
            session,
            by_symbol,
            symbol=entry.us_symbol,
            company_name=entry.company_name,
            kind=TickerKind.us_stock,
            sector=entry.sector,
            price_symbol=entry.us_symbol,
            us_ticker_id=None,
        )
        _upsert(
            session,
            by_symbol,
            symbol=entry.cdr_symbol,
            company_name=entry.company_name,
            kind=TickerKind.cdr,
            sector=entry.sector,
            price_symbol=entry.price_symbol,
            us_ticker_id=us.id,
        )
        keep.update((entry.us_symbol, entry.cdr_symbol))
    for symbol, name in BENCHMARKS:
        _upsert(
            session,
            by_symbol,
            symbol=symbol,
            company_name=name,
            kind=TickerKind.benchmark,
            sector=None,
            price_symbol=symbol,
            us_ticker_id=None,
        )
        keep.add(symbol)
    deactivated = sorted(
        symbol for symbol, ticker in by_symbol.items() if symbol not in keep and ticker.active
    )
    for symbol in deactivated:
        by_symbol[symbol].active = False
        session.add(by_symbol[symbol])
    session.commit()
    return SeedResult(
        us_stocks=len(entries),
        cdrs=len(entries),
        benchmarks=len(BENCHMARKS),
        deactivated=deactivated,
    )


def _upsert(
    session: Session,
    by_symbol: dict[str, Ticker],
    *,
    symbol: str,
    company_name: str,
    kind: TickerKind,
    sector: str | None,
    price_symbol: str,
    us_ticker_id: uuid.UUID | None,
) -> Ticker:
    ticker = by_symbol.get(symbol)
    if ticker is None:
        ticker = Ticker(symbol=symbol, company_name=company_name, kind=kind)
        by_symbol[symbol] = ticker
    elif ticker.kind is not kind:
        raise ValueError(
            f"{symbol} is already a {ticker.kind.value}; refusing to reseed it as {kind.value}"
        )
    ticker.company_name = company_name
    ticker.sector = sector
    ticker.price_symbol = price_symbol
    ticker.us_ticker_id = us_ticker_id
    # Existing universe names keep the flag `update_liquidity_flags` set; benchmarks are
    # never judged for liquidity, so they are always active.
    if kind is TickerKind.benchmark:
        ticker.active = True
    session.add(ticker)
    session.flush()
    return ticker
