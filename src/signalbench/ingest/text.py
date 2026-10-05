import re

from bs4 import BeautifulSoup, Tag

MAX_TEXT_CHARS = 96_000  # about 24K tokens, inside Jev's 32K state budget
DOCUMENT_SEPARATOR = "\n\n---\n\n"
DATE_PLACEHOLDER = "[date]"
_SIGNATURE_HEADINGS = frozenset({"signature", "signatures"})
_FORWARD_LOOKING = "forward-looking statements"
_HEADING_MAX_CHARS = 80

# A table is data, not layout, when at least half of its non-empty cells are numbers and it
# has at least 4 of them. Calibrated on 19 8-Ks (JPM, PFE, AAPL, MSFT, MCD, WMT and others):
# statement and supplement tables score 0.5-0.9; layout tables (Item headings, wrapped
# paragraphs, cover pages, exhibit indexes, footnotes) score below 0.45.
_NUMBER_CELL = re.compile(r"[\s$€£%().,/+\-–—]*\d[\d\s$€£%().,/+\-–—]*")
_NUMERIC_TABLE_MIN_CELLS = 4
_NUMERIC_TABLE_MIN_SHARE = 0.5

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_MONTH_ABBREVIATIONS = ("Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec")
_MONTH = "|".join(
    sorted(
        {*_MONTH_NAMES, *_MONTH_ABBREVIATIONS, *(name.upper() for name in _MONTH_NAMES)},
        key=len,
        reverse=True,
    )
)
_DAY = r"(?:3[01]|[12]\d|0?[1-9])"
# Calendar dates only. Bare years and periods ("fiscal 2026", "March 2026") are kept.
_DATE = re.compile(
    rf"""
      \b(?:{_MONTH})\.?\s+{_DAY}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}}\b)?          # January 14, 2026
    | \b{_DAY}(?:st|nd|rd|th)?\s+(?:{_MONTH})\.?,?\s+\d{{4}}\b                # 14 January 2026
    | (?<![\d/])(?:1[0-2]|0?[1-9])/{_DAY}/(?:\d{{4}}|\d{{2}})(?![\d/])        # 1/14/2026
    | (?<![\d-])\d{{4}}-(?:1[0-2]|0[1-9])-(?:3[01]|[12]\d|0[1-9])(?![\d-])  # 2026-01-14
    """,
    re.VERBOSE,
)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        if isinstance(tag, Tag):
            tag.decompose()
    _drop_numeric_tables(soup)
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def strip_boilerplate(text: str) -> str:
    lines = text.split("\n")
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip().lower() in _SIGNATURE_HEADINGS:
            lines = lines[:index]
            break
    kept: list[str] = []
    skip_next = False
    for line in lines:
        if skip_next:
            skip_next = False
            continue
        lowered = line.lower()
        if _FORWARD_LOOKING in lowered:
            if _is_heading(line):
                skip_next = True
                continue
            if "risks" in lowered or "uncertainties" in lowered:
                continue
        kept.append(line)
    return "\n".join(kept)


def replace_dates(text: str) -> str:
    return _DATE.sub(DATE_PLACEHOLDER, text)


def clean_document(html: str) -> str:
    return replace_dates(strip_boilerplate(html_to_text(html)))


def compose_filing_text(primary_html: str, exhibit_htmls: list[str]) -> str:
    parts = [clean_document(primary_html), *(clean_document(html) for html in exhibit_htmls)]
    return DOCUMENT_SEPARATOR.join(part for part in parts if part)[:MAX_TEXT_CHARS]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) <= _HEADING_MAX_CHARS and not stripped.endswith(".")


def _drop_numeric_tables(soup: BeautifulSoup) -> None:
    # Innermost first, so a layout table is judged without the numeric tables inside it.
    for table in reversed(soup.find_all("table")):
        if isinstance(table, Tag) and _is_numeric_table(table):
            table.decompose()


def _is_numeric_table(table: Tag) -> bool:
    numbers = words = 0
    for cell in table.find_all(["td", "th"]):
        content = " ".join(cell.get_text(" ").split())
        if _NUMBER_CELL.fullmatch(content):
            numbers += 1
        elif any(char.isalpha() for char in content):
            words += 1
    return (
        numbers >= _NUMERIC_TABLE_MIN_CELLS
        and numbers >= _NUMERIC_TABLE_MIN_SHARE * (numbers + words)
    )
