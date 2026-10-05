from signalbench.ingest.text import (
    DOCUMENT_SEPARATOR,
    MAX_TEXT_CHARS,
    compose_filing_text,
    html_to_text,
    replace_dates,
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


def _row(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


FINANCIAL_TABLE = (
    "<table>"
    + _row("(in millions)", "3Q26", "2Q26", "3Q25")
    + _row("Net revenue", "$", "46,014", "$", "45,678", "$", "42,654")
    + _row("Net income", "14,393", "15,001", "12,898")
    + _row("Diluted EPS", "$", "5.07", "$", "5.24", "$", "4.37")
    + "</table>"
)


def test_html_to_text_drops_financial_tables() -> None:
    html = f"<p>Net income of $14.4 billion.</p>{FINANCIAL_TABLE}<p>Contact: Investor Relations</p>"
    assert html_to_text(html) == "Net income of $14.4 billion.\nContact: Investor Relations"


def test_html_to_text_keeps_layout_tables() -> None:
    html = (
        "<table>"
        + _row("Item 2.02", "Results of Operations and Financial Condition")
        + "</table><table>"
        + _row("Jamie Dimon commented: “The Firm reported net income of $14.6 billion.”")
        + "</table><table>"
        + _row("Delaware", "1-5805", "13-2624428")
        + _row("(State of incorporation)", "(Commission File Number)", "(IRS Employer No.)")
        + "</table><table>"
        + _row("(1)", "Adjusted income is a non-GAAP measure.")
        + "</table>"
    )
    assert html_to_text(html) == (
        "Item 2.02\nResults of Operations and Financial Condition\n"
        "Jamie Dimon commented: “The Firm reported net income of $14.6 billion.”\n"
        "Delaware\n1-5805\n13-2624428\n"
        "(State of incorporation)\n(Commission File Number)\n(IRS Employer No.)\n"
        "(1)\nAdjusted income is a non-GAAP measure."
    )


def test_html_to_text_keeps_a_table_with_few_number_cells() -> None:
    html = "<table>" + _row("Revenue", "412") + _row("Stores", "7") + "</table>"
    assert html_to_text(html) == "Revenue\n412\nStores\n7"


def test_html_to_text_drops_numeric_table_nested_in_a_layout_table() -> None:
    html = f"<table><tr><td><p>Revenue grew 8%.</p>{FINANCIAL_TABLE}</td></tr></table>"
    assert html_to_text(html) == "Revenue grew 8%."


def test_replace_dates_covers_calendar_date_formats() -> None:
    cases = {
        "on January 14, 2026 the": "on [date] the",
        "on Jan. 14, 2026 the": "on [date] the",
        "on Sept. 30 2025 the": "on [date] the",
        "on 14 January 2026 the": "on [date] the",
        "on January 14 the": "on [date] the",
        "on March 1st the": "on [date] the",
        "DATED JULY 14, 2026": "DATED [date]",
        "on 1/14/2026 the": "on [date] the",
        "on 01/14/26 the": "on [date] the",
        "on 2026-01-14 the": "on [date] the",
        "ended June 30, 2026 and June 30, 2025": "ended [date] and [date]",
    }
    for text, expected in cases.items():
        assert replace_dates(text) == expected, text


def test_replace_dates_leaves_periods_numbers_and_times() -> None:
    for text in (
        "fiscal 2026",
        "fourth-quarter 2025",
        "full year 2025",
        "March 2026",
        "revenue of $1.14 billion",
        "3/4 of the portfolio",
        "Form 10-K",
        "Item 2.02",
        "page 1/3",
        "at 8:30 a.m. ET",
        "File No. 1-5805 and 13-2624428",
        "we may 10 times",
        "up 12.5% to 2,026",
    ):
        assert replace_dates(text) == text, text


def test_compose_replaces_dates_and_drops_tables_before_the_cap() -> None:
    filler = "<p>" + "a" * (MAX_TEXT_CHARS - 100) + "</p>"
    tail = "<p>Board approved on January 14, 2026.</p>"
    text = compose_filing_text(f"{FINANCIAL_TABLE * 200}{filler}{tail}", [])
    assert "46,014" not in text
    assert text.endswith("Board approved on [date].")
    assert len(text) <= MAX_TEXT_CHARS
