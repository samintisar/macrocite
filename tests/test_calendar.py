from datetime import date

import pytest

from signalbench.market.calendar import NyseSessions, Sessions


@pytest.fixture(scope="module")
def nyse() -> NyseSessions:
    return NyseSessions(start=date(2022, 1, 1))


def test_thanksgiving_2022_is_not_a_session(nyse: NyseSessions) -> None:
    assert nyse.sessions_between(date(2022, 11, 21), date(2022, 11, 28)) == [
        date(2022, 11, 21),
        date(2022, 11, 22),
        date(2022, 11, 23),
        date(2022, 11, 25),
        date(2022, 11, 28),
    ]
    assert nyse.is_session(date(2022, 11, 24)) is False
    assert nyse.is_session(date(2022, 11, 25)) is True


def test_next_sessions_are_strictly_after_the_day(nyse: NyseSessions) -> None:
    assert nyse.next_sessions(date(2022, 11, 23), 3) == [
        date(2022, 11, 25),
        date(2022, 11, 28),
        date(2022, 11, 29),
    ]
    # Juneteenth observed on Monday 2022-06-20.
    assert nyse.next_sessions(date(2022, 6, 17), 1) == [date(2022, 6, 21)]


def test_empty_range(nyse: NyseSessions) -> None:
    assert nyse.sessions_between(date(2022, 11, 28), date(2022, 11, 21)) == []


def test_nyse_sessions_satisfies_the_protocol(nyse: NyseSessions) -> None:
    calendar: Sessions = nyse
    assert isinstance(calendar.sessions_between(date(2022, 1, 3), date(2022, 1, 3))[0], date)
