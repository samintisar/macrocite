"""Spec 09: a crypto ticker kind for Kraken's BTC and ETH USD pairs

Revision ID: 0017_crypto_ticker_kind
Revises: 0016_bot_updates
Create Date: 2026-10-11 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_crypto_ticker_kind"
down_revision: str | None = "0016_bot_updates"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE tickerkind ADD VALUE IF NOT EXISTS 'crypto'")


def downgrade() -> None:
    # Postgres cannot drop one value from an enum type. Leaving 'crypto' in place is harmless:
    # the downgraded code never writes it, and no crypto rows may remain (delete them first).
    pass
