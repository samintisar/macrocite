import json
import time as time_module
from datetime import UTC, date, datetime, time
from typing import Any, cast

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
        time_module.sleep(0.15)
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
                tzinfo=UTC,
            ),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        session.commit()
        created += 1

    return created


def _lookup_cik_for_symbol(client: httpx.Client, headers: dict[str, str], symbol: str) -> str:
    payload = _company_tickers(client, headers)
    normalized_symbol = symbol.upper()
    for company in payload.values():
        if company["ticker"].upper() == normalized_symbol:
            return f"{int(company['cik_str']):010d}"
    raise ValueError(f"Ticker not found in SEC company_tickers.json: {symbol}")


_COMPANY_TICKERS: dict[str, Any] | None = None


def _company_tickers(client: httpx.Client, headers: dict[str, str]) -> dict[str, Any]:
    global _COMPANY_TICKERS
    if _COMPANY_TICKERS is None:
        _COMPANY_TICKERS = _get_json(client, COMPANY_TICKERS_URL, headers)
    return _COMPANY_TICKERS


def _get_json(client: httpx.Client, url: str, headers: dict[str, str]) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_get(client, url, headers).text))


def _get_text(client: httpx.Client, url: str, headers: dict[str, str]) -> str:
    return _get(client, url, headers).text


def _get(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    retries: int = 5,
) -> httpx.Response:
    last_error: httpx.HTTPStatusError | None = None
    for attempt in range(retries):
        response = client.get(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        last_error = httpx.HTTPStatusError(
            f"429 Too Many Requests for {url}",
            request=response.request,
            response=response,
        )
        retry_after = response.headers.get("Retry-After")
        try:
            delay = max(10.0, float(retry_after)) if retry_after is not None else 10.0
        except ValueError:
            delay = 10.0
        if retry_after == "0":
            delay = 0.0
        time_module.sleep(delay)
    assert last_error is not None
    raise last_error
