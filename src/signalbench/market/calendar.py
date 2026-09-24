"""NYSE sessions as plain dates. The only module that imports exchange_calendars."""

from datetime import date, timedelta
from typing import Any, Protocol

HISTORY_START = date(2010, 1, 1)


class Sessions(Protocol):
    def sessions_between(self, start: date, end: date) -> list[date]:
        """Sessions with start <= session <= end, in order."""
        ...

    def next_sessions(self, day: date, count: int) -> list[date]:
        """The first `count` sessions strictly after `day`."""
        ...

    def is_session(self, day: date) -> bool: ...


class NyseSessions:
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar("XNYS", start=start.isoformat())

    def sessions_between(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        return [stamp.date() for stamp in self._calendar.sessions_in_range(start, end)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        # Weekends plus the longest NYSE closure stay far inside 2 * count + 10 calendar days.
        horizon = day + timedelta(days=2 * count + 10)
        found = self.sessions_between(day + timedelta(days=1), horizon)
        if len(found) < count:
            raise ValueError(f"Only {len(found)} NYSE sessions known after {day}; need {count}")
        return found[:count]

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(day))
