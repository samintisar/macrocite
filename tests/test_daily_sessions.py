from datetime import UTC, date, datetime

from signalbench.market.calendar import DailySessions, Sessions


def test_every_calendar_day_is_a_session() -> None:
    calendar: Sessions = DailySessions()
    assert calendar.sessions_between(date(2024, 2, 27), date(2024, 3, 2)) == [
        date(2024, 2, 27),
        date(2024, 2, 28),
        date(2024, 2, 29),
        date(2024, 3, 1),
        date(2024, 3, 2),
    ]
    assert calendar.sessions_between(date(2024, 3, 2), date(2024, 3, 1)) == []
    assert calendar.is_session(date(2024, 12, 25)) is True


def test_next_sessions_are_the_following_days() -> None:
    assert DailySessions().next_sessions(date(2024, 12, 30), 3) == [
        date(2024, 12, 31),
        date(2025, 1, 1),
        date(2025, 1, 2),
    ]


def test_sessions_start_at_the_first_day_and_close_at_utc_midnight() -> None:
    calendar = DailySessions(start=date(2010, 1, 1))
    assert calendar.sessions_between(date(2009, 12, 30), date(2010, 1, 2)) == [
        date(2010, 1, 1),
        date(2010, 1, 2),
    ]
    assert calendar.is_session(date(2009, 12, 31)) is False
    assert calendar.session_closes(date(2024, 3, 1), date(2024, 3, 1)) == [
        (date(2024, 3, 1), datetime(2024, 3, 2, 0, 0, tzinfo=UTC))
    ]
