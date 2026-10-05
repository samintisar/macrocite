from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from signalbench.market.calendar import NyseSessions
from signalbench.market.legal_close import LegalCloses
from strategy_helpers import WeekdaySessions

NEW_YORK = ZoneInfo("America/New_York")
TUESDAY = date(2024, 1, 2)
CLOSES = LegalCloses(WeekdaySessions().session_closes(date(2024, 1, 1), date(2024, 1, 31)))


def _ny(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=NEW_YORK)


def test_before_the_close_is_that_sessions_close() -> None:
    assert CLOSES.of(_ny(TUESDAY, 15, 59)) == TUESDAY
    assert CLOSES.of(_ny(TUESDAY, 4, 0)) == TUESDAY  # pre-market


def test_at_or_after_the_close_is_the_next_session() -> None:
    assert CLOSES.of(_ny(TUESDAY, 16, 0)) == date(2024, 1, 3)  # strictly after the close
    assert CLOSES.of(_ny(TUESDAY, 16, 5)) == date(2024, 1, 3)


def test_a_weekend_document_waits_for_monday() -> None:
    assert CLOSES.of(_ny(date(2024, 1, 6), 11, 0)) == date(2024, 1, 8)


def test_utc_timestamps_are_compared_as_instants() -> None:
    # 21:05 UTC is 16:05 EST: after Tuesday's close.
    assert CLOSES.of(datetime(2024, 1, 2, 21, 5, tzinfo=UTC)) == date(2024, 1, 3)
    assert CLOSES.of(datetime(2024, 1, 2, 20, 55, tzinfo=UTC)) == TUESDAY


def test_after_the_last_known_close_is_none() -> None:
    assert CLOSES.of(_ny(date(2024, 1, 31), 16, 30)) is None


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        CLOSES.of(datetime(2024, 1, 2, 12, 0))  # noqa: DTZ001  # naive on purpose


def test_an_early_close_moves_the_cutoff() -> None:
    nyse = NyseSessions(start=date(2022, 1, 1))
    closes = LegalCloses(nyse.session_closes(date(2022, 11, 21), date(2022, 11, 30)))
    # 2022-11-25, the day after Thanksgiving, closed at 13:00 ET.
    assert closes.of(_ny(date(2022, 11, 25), 12, 59)) == date(2022, 11, 25)
    assert closes.of(_ny(date(2022, 11, 25), 13, 30)) == date(2022, 11, 28)
    # Thanksgiving itself is not a session.
    assert closes.of(_ny(date(2022, 11, 24), 10, 0)) == date(2022, 11, 25)


def test_nyse_session_closes_are_aware_utc_instants() -> None:
    nyse = NyseSessions(start=date(2022, 1, 1))
    closes = dict(nyse.session_closes(date(2022, 11, 23), date(2022, 11, 28)))
    assert sorted(closes) == [date(2022, 11, 23), date(2022, 11, 25), date(2022, 11, 28)]
    assert closes[date(2022, 11, 23)] == datetime(2022, 11, 23, 21, 0, tzinfo=UTC)
    assert closes[date(2022, 11, 25)] == datetime(2022, 11, 25, 18, 0, tzinfo=UTC)
    assert nyse.session_closes(date(2022, 11, 28), date(2022, 11, 21)) == []


def test_nyse_ranges_that_start_before_the_calendar_are_clamped() -> None:
    nyse = NyseSessions()  # starts 2010-01-01, a holiday; the first session is 2010-01-04
    assert nyse.sessions_between(date(2010, 1, 1), date(2010, 1, 6)) == [
        date(2010, 1, 4), date(2010, 1, 5), date(2010, 1, 6),
    ]
    assert [day for day, _ in nyse.session_closes(date(2010, 1, 1), date(2010, 1, 5))] == [
        date(2010, 1, 4), date(2010, 1, 5),
    ]
