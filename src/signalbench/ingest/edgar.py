import json
import logging
import time as time_module
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, cast

import httpx
from bs4 import BeautifulSoup, Tag
from sqlmodel import Session, col, select

from signalbench.db.models import DocType, DocumentTicker, RawDocument, Ticker
from signalbench.ingest.ratelimit import RateLimiter
from signalbench.ingest.text import compose_filing_text

logger = logging.getLogger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik10}.json"
SUBMISSIONS_PAGE_URL_TEMPLATE = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{document}"
)
FILING_INDEX_URL_TEMPLATE = (
    "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_nodash}/{accession}-index.htm"
)
EIGHT_K_FORMS = frozenset({"8-K", "8-K/A"})
DEFAULT_SINCE = date(2016, 1, 1)
# SEC allows 10 requests/second; stay under it.
SEC_LIMITER = RateLimiter(calls=8, period=1.0)


@dataclass(frozen=True)
class FilingRef:
    accession_number: str
    form: str
    filing_date: date
    acceptance_at: datetime | None
    items: str
    primary_document: str

    @property
    def available_on(self) -> date:
        return self.acceptance_at.date() if self.acceptance_at is not None else self.filing_date


def parse_acceptance(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def refs_from_columns(columns: dict[str, list[Any]]) -> list[FilingRef]:
    count = len(columns["accessionNumber"])
    acceptance = columns.get("acceptanceDateTime", [""] * count)
    items = columns.get("items", [""] * count)
    return [
        FilingRef(
            accession_number=str(columns["accessionNumber"][index]),
            form=str(columns["form"][index]),
            filing_date=date.fromisoformat(str(columns["filingDate"][index])),
            acceptance_at=parse_acceptance(str(acceptance[index] or "")),
            items=str(items[index] or ""),
            primary_document=str(columns["primaryDocument"][index]),
        )
        for index in range(count)
    ]


def exhibit_documents(index_html: str) -> list[str]:
    """File names of EX-99.* documents listed in a filing's -index.htm page, in order."""
    soup = BeautifulSoup(index_html, "lxml")
    names: list[str] = []
    for table in soup.find_all("table", class_="tableFile"):
        if not isinstance(table, Tag):
            continue
        for row in table.find_all("tr"):
            if not isinstance(row, Tag):
                continue
            cells = row.find_all("td")
            if len(cells) < 4 or not cells[3].get_text(strip=True).upper().startswith("EX-99"):
                continue
            link = cells[2].find("a")
            if isinstance(link, Tag) and link.get("href"):
                names.append(str(link["href"]).rsplit("/", 1)[-1])
    return names


def filing_refs(
    client: httpx.Client,
    headers: dict[str, str],
    cik10: str,
    since: date,
    limiter: RateLimiter,
) -> list[FilingRef]:
    submissions = _get_json(client, SUBMISSIONS_URL_TEMPLATE.format(cik10=cik10), headers, limiter)
    refs = refs_from_columns(submissions["filings"]["recent"])
    for page in submissions["filings"].get("files", []):
        if date.fromisoformat(str(page["filingTo"])) < since:
            continue
        url = SUBMISSIONS_PAGE_URL_TEMPLATE.format(name=page["name"])
        refs.extend(refs_from_columns(_get_json(client, url, headers, limiter)))
    return refs


def ingest_eight_ks_for_symbol(
    session: Session,
    symbol: str,
    client: httpx.Client,
    user_agent: str,
    since: date = DEFAULT_SINCE,
    cik10: str | None = None,
    limiter: RateLimiter = SEC_LIMITER,
) -> int:
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    headers = {"User-Agent": user_agent}
    resolved_cik = cik10 or _lookup_cik_for_symbol(client, headers, symbol, limiter)

    created = 0
    for ref in filing_refs(client, headers, resolved_cik, since, limiter):
        if ref.form not in EIGHT_K_FORMS or ref.available_on < since:
            continue
        existing = session.exec(
            select(RawDocument).where(
                RawDocument.source == "sec_edgar",
                RawDocument.external_id == ref.accession_number,
            )
        ).first()
        if existing is not None:
            continue

        primary_html = _get_text(client, _archive_url(resolved_cik, ref), headers, limiter)
        exhibits = _fetch_exhibits(client, headers, resolved_cik, ref, limiter)
        document = RawDocument(
            source="sec_edgar",
            external_id=ref.accession_number,
            doc_type=DocType.eight_k,
            url=_archive_url(resolved_cik, ref),
            title=ref.primary_document,
            raw_text=primary_html,
            text=compose_filing_text(primary_html, exhibits),
            items=ref.items or None,
            acceptance_at=ref.acceptance_at,
            published_at=ref.acceptance_at
            or datetime.combine(ref.filing_date, time.min, tzinfo=UTC),
        )
        session.add(document)
        session.flush()
        session.add(DocumentTicker(document_id=document.id, ticker_id=ticker.id))
        session.commit()
        created += 1

    return created


def backfill_filing_text(
    session: Session,
    symbol: str,
    client: httpx.Client,
    user_agent: str,
    cik10: str | None = None,
    limiter: RateLimiter = SEC_LIMITER,
) -> int:
    """Fill acceptance_at, items, and text for SEC documents stored before spec 01."""
    ticker = session.exec(select(Ticker).where(Ticker.symbol == symbol)).one()
    document_ids = list(
        session.exec(
            select(DocumentTicker.document_id).where(DocumentTicker.ticker_id == ticker.id)
        ).all()
    )
    if not document_ids:
        return 0
    documents = [
        document
        for document in session.exec(
            select(RawDocument).where(
                RawDocument.source == "sec_edgar",
                col(RawDocument.id).in_(document_ids),
            )
        ).all()
        if document.text is None
    ]
    if not documents:
        return 0

    headers = {"User-Agent": user_agent}
    resolved_cik = cik10 or _lookup_cik_for_symbol(client, headers, symbol, limiter)
    since = min(document.published_at.date() for document in documents)
    refs = {
        ref.accession_number: ref
        for ref in filing_refs(client, headers, resolved_cik, since, limiter)
    }
    updated = 0
    for document in documents:
        ref = refs.get(document.external_id)
        if ref is None:
            logger.warning("No submissions entry for %s; text not backfilled", document.external_id)
            continue
        exhibits = _fetch_exhibits(client, headers, resolved_cik, ref, limiter)
        document.text = compose_filing_text(document.raw_text, exhibits)
        document.items = ref.items or None
        document.acceptance_at = ref.acceptance_at
        if ref.acceptance_at is not None:
            document.published_at = ref.acceptance_at
        session.add(document)
        session.commit()
        updated += 1
    return updated


def _fetch_exhibits(
    client: httpx.Client,
    headers: dict[str, str],
    cik10: str,
    ref: FilingRef,
    limiter: RateLimiter,
) -> list[str]:
    index_url = FILING_INDEX_URL_TEMPLATE.format(
        cik_no_zeros=str(int(cik10)),
        accession_nodash=ref.accession_number.replace("-", ""),
        accession=ref.accession_number,
    )
    try:
        index_html = _get_text(client, index_url, headers, limiter)
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            logger.warning("No filing index for %s", ref.accession_number)
            return []
        raise
    return [
        _get_text(client, _archive_url(cik10, ref, name), headers, limiter)
        for name in exhibit_documents(index_html)
    ]


def _archive_url(cik10: str, ref: FilingRef, document: str | None = None) -> str:
    return ARCHIVE_URL_TEMPLATE.format(
        cik_no_zeros=str(int(cik10)),
        accession_nodash=ref.accession_number.replace("-", ""),
        document=document or ref.primary_document,
    )


def _lookup_cik_for_symbol(
    client: httpx.Client,
    headers: dict[str, str],
    symbol: str,
    limiter: RateLimiter,
) -> str:
    payload = _company_tickers(client, headers, limiter)
    normalized_symbol = symbol.upper()
    for company in payload.values():
        if company["ticker"].upper() == normalized_symbol:
            return f"{int(company['cik_str']):010d}"
    raise ValueError(f"Ticker not found in SEC company_tickers.json: {symbol}")


_COMPANY_TICKERS: dict[str, Any] | None = None


def _company_tickers(
    client: httpx.Client,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> dict[str, Any]:
    global _COMPANY_TICKERS
    if _COMPANY_TICKERS is None:
        _COMPANY_TICKERS = _get_json(client, COMPANY_TICKERS_URL, headers, limiter)
    return _COMPANY_TICKERS


def _get_json(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_get(client, url, headers, limiter).text))


def _get_text(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
) -> str:
    return _get(client, url, headers, limiter).text


def _get(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    limiter: RateLimiter,
    retries: int = 5,
) -> httpx.Response:
    last_error: httpx.HTTPStatusError | None = None
    for _attempt in range(retries):
        limiter.wait()
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
