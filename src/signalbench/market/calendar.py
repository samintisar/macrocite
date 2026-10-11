"""Sessions as plain dates: NYSE and Cboe Canada (the only module that imports
exchange_calendars), and every calendar day for crypto (spec 09)."""

from datetime import UTC, date, datetime, time, timedelta
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

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """(session, its close as an aware datetime) for sessions with start <= session <= end."""
        ...


class NyseSessions:
    """The XNYS calendar from exchange_calendars, exposed as `datetime.date` values."""

    CODE = "XNYS"
    NAME = "NYSE"

    def __init__(self, start: date = HISTORY_START) -> None:
        import exchange_calendars as xcals

        self._calendar: Any = xcals.get_calendar(self.CODE, start=start.isoformat())
        self._first: date = self._calendar.first_session.date()

    def sessions_between(self, start: date, end: date) -> list[date]:
        start = max(start, self._first)  # exchange_calendars rejects dates before its first session
        if end < start:
            return []
        return [stamp.date() for stamp in self._calendar.sessions_in_range(start, end)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        # Weekends plus the longest NYSE closure stay far inside 2 * count + 10 calendar days.
        horizon = day + timedelta(days=2 * count + 10)
        found = self.sessions_between(day + timedelta(days=1), horizon)
        if len(found) < count:
            raise ValueError(
                f"Only {len(found)} {self.NAME} sessions known after {day}; need {count}"
            )
        return found[:count]

    def is_session(self, day: date) -> bool:
        return bool(self._calendar.is_session(day))

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        """Real closes in UTC, including early closes (13:00 ET on some holiday eves)."""
        if end < start:
            return []
        closes = self._calendar.closes.loc[start.isoformat() : end.isoformat()]
        return [(stamp.date(), close.to_pydatetime()) for stamp, close in closes.items()]


class CboeCanadaSessions(NyseSessions):
    """Cboe Canada, where the CDRs trade (spec 05). exchange_calendars has no Cboe Canada
    calendar, so Toronto's XTSE stands in for it: the same holidays and hours."""

    CODE = "XTSE"
    NAME = "Cboe Canada"


class DailySessions:
    """Every calendar day is a session (spec 09: crypto trades all week). A session closes at
    00:00 UTC the next day, when Kraken's daily candle for it is final."""

    def __init__(self, start: date = HISTORY_START) -> None:
        self._first = start

    def sessions_between(self, start: date, end: date) -> list[date]:
        start = max(start, self._first)
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]

    def next_sessions(self, day: date, count: int) -> list[date]:
        return [day + timedelta(days=i) for i in range(1, count + 1)]

    def is_session(self, day: date) -> bool:
        return day >= self._first

    def session_closes(self, start: date, end: date) -> list[tuple[date, datetime]]:
        return [
            (day, datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=UTC))
            for day in self.sessions_between(start, end)
        ]
