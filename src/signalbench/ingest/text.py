from bs4 import BeautifulSoup, Tag

MAX_TEXT_CHARS = 96_000  # about 24K tokens, inside Jev's 32K state budget
DOCUMENT_SEPARATOR = "\n\n---\n\n"
_SIGNATURE_HEADINGS = frozenset({"signature", "signatures"})
_FORWARD_LOOKING = "forward-looking statements"
_HEADING_MAX_CHARS = 80


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header"]):
        if isinstance(tag, Tag):
            tag.decompose()
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


def clean_document(html: str) -> str:
    return strip_boilerplate(html_to_text(html))


def compose_filing_text(primary_html: str, exhibit_htmls: list[str]) -> str:
    parts = [clean_document(primary_html), *(clean_document(html) for html in exhibit_htmls)]
    return DOCUMENT_SEPARATOR.join(part for part in parts if part)[:MAX_TEXT_CHARS]


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) <= _HEADING_MAX_CHARS and not stripped.endswith(".")
