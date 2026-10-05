"""A small made-up 8-K for `signalbench jev test`: no real company, no dates, public-style text."""

from signalbench.jev.questions import build_state

FIXTURE_COMPANY = "Example Holdings"
FIXTURE_SYMBOL = "EXMP"
FIXTURE_ITEMS = "2.02,9.01"
FIXTURE_TEXT = (
    "Item 2.02 Results of Operations and Financial Condition.\n"
    "Example Holdings Inc. announced results for its fiscal quarter. Revenue grew 18 percent "
    "from the prior-year quarter to a record level, operating margin widened, and the company "
    "raised its full-year revenue and earnings outlook. The board also approved a new share "
    "repurchase program.\n\n"
    "Item 9.01 Financial Statements and Exhibits.\n"
    "Exhibit 99.1: Press release issued by Example Holdings Inc."
)


def fixture_state() -> str:
    return build_state(FIXTURE_COMPANY, FIXTURE_SYMBOL, "filings", FIXTURE_ITEMS, FIXTURE_TEXT)
