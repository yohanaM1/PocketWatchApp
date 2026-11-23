"""agent query columns

Revision ID: 7f3c2a9d41e6
Revises: 25cdb3a1dabd
Create Date: 2026-10-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3c2a9d41e6'
down_revision: Union[str, Sequence[str], None] = '25cdb3a1dabd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the plan, slots, final results and audit fields to queries."""
    op.add_column("queries", sa.Column("plan", sa.JSON(), nullable=True))
    op.add_column("queries", sa.Column("extra_sentence", sa.Text(), nullable=True))
    op.add_column("queries", sa.Column("slots", sa.JSON(), nullable=True))
    op.add_column("queries", sa.Column("final_results", sa.JSON(), nullable=True))
    op.add_column("queries", sa.Column("failure_reason", sa.String(500), nullable=True))
    op.add_column("queries", sa.Column("attempts", sa.Integer(), server_default="1", nullable=False))
    # audit fields: what answered, against which "today", how long it took
    op.add_column("queries", sa.Column("llm_model", sa.String(100), nullable=True))
    op.add_column("queries", sa.Column("prompt_version", sa.String(20), nullable=True))
    op.add_column("queries", sa.Column("as_of", sa.Date(), nullable=True))
    op.add_column("queries", sa.Column("latency_ms", sa.Integer(), nullable=True))
    # ix_queries_user_id stays: without it MySQL would use this index for the user_id FK,
    # and dropping it in downgrade() would then fail.
    op.create_index("ix_queries_user_created", "queries", ["user_id", "created_at"])


def downgrade() -> None:
    """Remove the agent columns and the history index."""
    op.drop_index("ix_queries_user_created", table_name="queries")
    for col in ("latency_ms", "as_of", "prompt_version", "llm_model",
                "attempts", "failure_reason", "final_results", "slots", "extra_sentence", "plan"):
        op.drop_column("queries", col)
