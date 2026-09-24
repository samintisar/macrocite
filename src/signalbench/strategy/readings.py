"""The port through which Jev readings (spec 03) reach decide()."""

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
