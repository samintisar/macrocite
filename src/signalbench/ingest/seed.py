from pathlib import Path

import yaml
from sqlmodel import Session, select

from signalbench.db.models import Ticker


def seed_watchlist(session: Session, path: Path) -> int:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = payload["tickers"]
    for entry in entries:
        existing = session.exec(select(Ticker).where(Ticker.symbol == entry["symbol"])).first()
        if existing is None:
            session.add(
                Ticker(
                    symbol=entry["symbol"],
                    company_name=entry["company_name"],
                    active=True,
                )
            )
        else:
            existing.company_name = entry["company_name"]
            existing.active = True
            session.add(existing)
    session.commit()
    return len(entries)
