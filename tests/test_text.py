from signalbench.ingest.text import (
    DOCUMENT_SEPARATOR,
    MAX_TEXT_CHARS,
    compose_filing_text,
    html_to_text,
    strip_boilerplate,
)


def test_html_to_text_drops_scripts_styles_and_xbrl_header() -> None:
    html = (
        "<html><head><style>p { color: red }</style><script>var x = 1;</script></head>"
        "<body><ix:header><ix:hidden>HIDDEN XBRL</ix:hidden></ix:header>"
        "<p>Item 2.02   Results of   Operations</p><div>Second paragraph</div></body></html>"
    )
    assert html_to_text(html) == "Item 2.02 Results of Operations\nSecond paragraph"


def test_strip_boilerplate_removes_signature_block() -> None:
    text = (
        "Item 8.01 Other Events\n"
        "The company announced a buyback.\n"
        "SIGNATURES\n"
        "Pursuant to the requirements of the Securities Exchange Act of 1934\n"
        "Apple Inc."
    )
    assert strip_boilerplate(text) == "Item 8.01 Other Events\nThe company announced a buyback."


def test_strip_boilerplate_removes_safe_harbor_heading_and_its_paragraph() -> None:
    text = (
        "Revenue grew 8%.\n"
        "Forward-Looking Statements\n"
        "This release contains statements about future plans.\n"
        "Contact: Investor Relations"
    )
    assert strip_boilerplate(text) == "Revenue grew 8%.\nContact: Investor Relations"


def test_strip_boilerplate_removes_inline_safe_harbor_paragraph() -> None:
    paragraph = (
        "This press release contains forward-looking statements within the meaning of the "
        "Private Securities Litigation Reform Act of 1995. These statements involve risks and "
        "uncertainties that could cause actual results to differ materially."
    )
    assert strip_boilerplate(f"Revenue grew 8%.\n{paragraph}") == "Revenue grew 8%."


def test_strip_boilerplate_keeps_ordinary_sentences() -> None:
    text = "We will not update forward-looking statements.\nRevenue grew 8%."
    assert strip_boilerplate(text) == text


def test_compose_joins_documents_and_caps_length() -> None:
    text = compose_filing_text("<p>Primary</p>", ["<p>Exhibit one</p>", "<p></p>"])
    assert text == f"Primary{DOCUMENT_SEPARATOR}Exhibit one"
    long_text = compose_filing_text("<p>" + "a" * (MAX_TEXT_CHARS + 500) + "</p>", [])
    assert len(long_text) == MAX_TEXT_CHARS
