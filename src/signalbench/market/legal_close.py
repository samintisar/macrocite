"""Legal close (overview, Shared terms): the first NYSE session close strictly after a moment."""

from bisect import bisect_right
from collections.abc import Sequence
from datetime import date, datetime


class LegalCloses:
    """Maps a document's acceptance or publish time to the session whose close first follows it.

    Built once from `Sessions.session_closes()`, so early closes are honoured and no calendar
    lookup happens per document.
    """

    def __init__(self, closes: Sequence[tuple[date, datetime]]) -> None:
        self._sessions = [session for session, _ in closes]
        self._closes = [close for _, close in closes]
        if any(close.utcoffset() is None for close in self._closes):
            raise ValueError("session closes must be timezone-aware")
        if any(a >= b for a, b in zip(self._closes, self._closes[1:], strict=False)):
            raise ValueError("session closes must strictly increase")

    def of(self, moment: datetime) -> date | None:
        """The first session whose close is strictly after `moment`, or None if that session is
        past the last known close (the document is not visible yet)."""
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError(f"moment must be timezone-aware, got naive {moment.isoformat()}")
        index = bisect_right(self._closes, moment)
        return self._sessions[index] if index < len(self._sessions) else None
