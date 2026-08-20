import json
from datetime import date, datetime, time, timezone

import httpx
from sqlmodel import Session, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
ARCHIVE_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{primary_document}"
)


def ingest_eight_ks_for_symbol(
    session: Session,
    symbol: str,
    client: httpx.Client,
    user_agent: str,
) -> int:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    headers = {"User-Agent": user_agent}

    cik10 = _lookup_cik_for_symbol(client, headers, symbol)
    submissions = _get_json(client, SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10), headers)
    recent = submissions["filings"]["recent"]

    created = 0
    for accession_number, form, filing_date, primary_document in zip(
        recent["accessionNumber"],
        recent["form"],
        recent["filingDate"],
        recent["primaryDocument"],
        strict=True,
    ):
        if form != "8-K":
            continue

        existing = session.exec(
            select(RawDocument).where(
                RawDocument.source == "sec_edgar",
                RawDocument.external_id == accession_number,
            )
        ).first()
        if existing is not None:
            continue

        archive_url = ARCHIVE_URL_TEMPLATE.format(
            cik_no_zeros=str(int(cik10)),
            accession_nodash=accession_number.replace("-", ""),
            primary_document=primary_document,
        )
        raw_text = _get_text(client, archive_url, headers)
        document = RawDocument(
            source="sec_edgar",
            external_id=accession_number,
            doc_type=DocType.eight_k,
            url=archive_url,
            title=primary_document,
            raw_text=raw_text,
            published_at=datetime.combine(
                date.fromisoformat(filing_date),
                time.min,
                tzinfo=timezone.utc,
            ),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        created += 1

    session.commit()
    return created


def _lookup_cik_for_symbol(client: httpx.Client, headers: dict[str, str], symbol: str) -> str:
    payload = _get_json(client, COMPANY_TICKERS_URL, headers)
    normalized_symbol = symbol.upper()
    for company in payload.values():
        if company["ticker"].upper() == normalized_symbol:
            return f"{int(company['cik_str']):010d}"
    raise ValueError(f"Ticker not found in SEC company_tickers.json: {symbol}")


def _get_json(client: httpx.Client, url: str, headers: dict[str, str]) -> dict:
    response = client.get(url, headers=headers)
    response.raise_for_status()
    return json.loads(response.text)


def _get_text(client: httpx.Client, url: str, headers: dict[str, str]) -> str:
    response = client.get(url, headers=headers)
    response.raise_for_status()
    return response.text
