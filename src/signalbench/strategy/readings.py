"""The port through which Jev readings (spec 03) reach decide()."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class ReadingsView(Protocol):
    def blocked(self, symbol: str, as_of: date) -> bool:
        """A negative document blocks entry (only when the Jev filter is on)."""
        ...

    def catalyst(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 10 sessions; ranks the candidate first."""
        ...

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        """A positive document in the last 3 sessions; feeds the Sentiment setup."""
        ...


class NullReadingsView:
    """Spec 02: no blocks, no catalysts, no sentiment triggers."""

    def blocked(self, symbol: str, as_of: date) -> bool:
        return False

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return False

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return False


POSITIVE_MIN = 0.70  # fixed, not fitted: it triggers a setup (spec 03)
ROUTINE_MAX = 0.50
TRIGGER_SESSIONS = 3
CATALYST_SESSIONS = 10
BLOCK_SESSIONS = 10


@dataclass(frozen=True)
class DocumentReading:
    """One Jev reading of one document for one symbol, keyed by the document's legal close."""

    legal_close: date
    p_negative: float
    p_neutral: float
    p_positive: float
    p_routine: float
    event_type: str
    document_id: str


def is_positive(reading: DocumentReading) -> bool:
    return reading.p_positive >= POSITIVE_MIN and reading.p_routine < ROUTINE_MAX


class JevReadingsView:
    """Spec 03 readings for decide(). Pure: built once from precomputed readings and sessions.

    At `as_of` it sees only documents whose legal close is on or before `as_of`. "The last N
    sessions" are the N sessions ending with `as_of`. `sessions` must reach at least
    BLOCK_SESSIONS sessions before the first as-of date. `block_theta` is None when the
    filter is information-only: then nothing is blocked.
    """

    def __init__(
        self,
        readings: Mapping[str, Sequence[DocumentReading]],
        sessions: Sequence[date],
        block_theta: float | None,
    ) -> None:
        self._sessions = list(sessions)
        self._readings = {
            symbol: sorted(rows, key=lambda r: (r.legal_close, r.document_id))
            for symbol, rows in readings.items()
        }
        self._closes = {
            symbol: [r.legal_close for r in rows] for symbol, rows in self._readings.items()
        }
        self._block_theta = block_theta

    def window(self, symbol: str, as_of: date, sessions: int) -> list[DocumentReading]:
        """Readings whose legal close falls in the `sessions` sessions ending at `as_of`."""
        last = bisect_right(self._sessions, as_of) - 1
        if last < 0 or symbol not in self._readings:
            return []
        first = self._sessions[max(0, last - sessions + 1)]
        closes = self._closes[symbol]
        return self._readings[symbol][bisect_left(closes, first) : bisect_right(closes, as_of)]

    def blocked(self, symbol: str, as_of: date) -> bool:
        theta = self._block_theta
        if theta is None:
            return False
        return any(r.p_negative >= theta for r in self.window(symbol, as_of, BLOCK_SESSIONS))

    def catalyst(self, symbol: str, as_of: date) -> bool:
        return any(is_positive(r) for r in self.window(symbol, as_of, CATALYST_SESSIONS))

    def sentiment_trigger(self, symbol: str, as_of: date) -> bool:
        return any(is_positive(r) for r in self.window(symbol, as_of, TRIGGER_SESSIONS))

    def max_p_negative(self, symbol: str, as_of: date) -> float | None:
        """The filter decision's input: max p_negative over the block window, None if no reading."""
        values = [r.p_negative for r in self.window(symbol, as_of, BLOCK_SESSIONS)]
        return max(values) if values else None
