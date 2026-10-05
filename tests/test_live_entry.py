"""Spec 05, Live CDR sizing: the entry session is the next Cboe Canada session (XTSE as the
proxy), and a signal expires at its close. Real exchange calendars, no network."""

from datetime import UTC, date, datetime

import pytest

from signalbench.live.sizing import entry_window
from signalbench.market.calendar import CboeCanadaSessions, NyseSessions


@pytest.fixture(scope="module")
def calendars() -> tuple[NyseSessions, CboeCanadaSessions]:
    return NyseSessions(start=date(2026, 1, 1)), CboeCanadaSessions(start=date(2026, 1, 1))


def test_cboe_canada_uses_the_toronto_calendar(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    nyse, cboe = calendars
    thanksgiving = date(2026, 10, 12)  # Canadian Thanksgiving: NYSE open, Toronto closed
    assert (nyse.is_session(thanksgiving), cboe.is_session(thanksgiving)) == (True, False)
    us_thanksgiving = date(2026, 11, 26)
    assert (nyse.is_session(us_thanksgiving), cboe.is_session(us_thanksgiving)) == (False, True)


def test_the_entry_session_is_the_next_cboe_session_and_expires_at_its_close(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 10, 6), *calendars)
    assert (window.session, window.note) == (date(2026, 10, 7), None)
    assert window.expires_at == datetime(2026, 10, 7, 20, 0, tzinfo=UTC)  # 16:00 Toronto


def test_a_cboe_holiday_moves_the_entry_and_the_expiry_and_says_so(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 10, 9), *calendars)  # Friday before Canadian Thanksgiving
    assert window.session == date(2026, 10, 13)
    assert window.expires_at == datetime(2026, 10, 13, 20, 0, tzinfo=UTC)
    assert window.note == "Cboe Canada is closed on Mon 12 Oct: place it on Tue 13 Oct."


def test_a_us_holiday_does_not_move_the_entry(
    calendars: tuple[NyseSessions, CboeCanadaSessions],
) -> None:
    window = entry_window(date(2026, 11, 25), *calendars)  # the eve of US Thanksgiving
    assert (window.session, window.note) == (date(2026, 11, 26), None)
