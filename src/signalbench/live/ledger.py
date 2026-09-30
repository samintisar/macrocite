"""The ledger (spec 04, Interface): the one class the scan, the bot, and the CLI use.

Built in layers: live/book.py (fills, cash, voids, signals, exit alerts), then live/levels.py
(prices, stops, splits), then this module (positions, equity, the pause, the tax report, and
the scale-up check).
"""

from signalbench.live.book import LedgerError
from signalbench.live.levels import LedgerLevels

__all__ = ["Ledger", "LedgerError"]


class Ledger(LedgerLevels):
    """Everything derived from what the owner did on Wealthsimple."""
