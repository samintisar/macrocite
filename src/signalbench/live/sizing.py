"""Where and how big an entry is placed on the CDR (spec 05, Live CDR sizing).

Pure: Decimal in, Decimal out, no database. The entry session is the next Cboe Canada session
after the signal (the XTSE calendar as the proxy), and the signal expires at its close.
"""

from dataclasses import dataclass
from datetime import date, datetime

from signalbench.market.calendar import Sessions


@dataclass(frozen=True)
class EntryWindow:
    session: date  # the Cboe Canada session to place the order in
    expires_at: datetime  # that session's close
    note: str | None  # set when Cboe Canada is closed on the next NYSE session


def _day(day: date) -> str:
    return f"{day:%a} {day.day:02d} {day:%b}"


def entry_window(as_of: date, nyse: Sessions, cboe: Sessions) -> EntryWindow:
    """The next Cboe Canada session after the signal session `as_of`. When Cboe Canada is
    closed on the next NYSE session, the entry and the expiry move to its next session."""
    session = cboe.next_sessions(as_of, 1)[0]
    [(_, close)] = cboe.session_closes(session, session)
    us_next = nyse.next_sessions(as_of, 1)[0]
    note = None
    if session > us_next:
        note = f"Cboe Canada is closed on {_day(us_next)}: place it on {_day(session)}."
    return EntryWindow(session=session, expires_at=close, note=note)
