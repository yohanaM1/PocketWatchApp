"""renormalize merchant names

Data only: re-runs the merchant cleanup rules (possessives, UMD spellings) over stored
transactions and moves category corrections to the new names.

Revision ID: 3c2078f83051
Revises: 7f3c2a9d41e6
Create Date: 2026-11-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

from app.services.merchant_backfill import renormalize_merchants


# revision identifiers, used by Alembic.
revision: str = '3c2078f83051'
down_revision: Union[str, Sequence[str], None] = '7f3c2a9d41e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Recompute merchant names with the current rules and rename corrections to match.

    Uses the app's rules as they are when it runs; a later rule change needs its own migration.
    """
    renormalize_merchants(op.get_bind())


def downgrade() -> None:
    """No-op: the old names aren't kept, and rows work with either version of the rules."""
