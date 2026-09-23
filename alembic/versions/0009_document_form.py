"""Record the SEC form (8-K or 8-K/A) on raw documents

Revision ID: 0009_document_form
Revises: 0008_swing_data_foundation
Create Date: 2026-09-23 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_form"
down_revision: str | None = "0008_swing_data_foundation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("raw_documents", sa.Column("form", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("raw_documents", "form")
