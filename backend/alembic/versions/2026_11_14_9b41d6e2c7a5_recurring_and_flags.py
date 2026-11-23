"""recurring series and anomaly flag columns

Schema only: detection runs from scripts/refresh_insights.py (docker-entrypoint.sh runs it after
migrations), so this migration never depends on detection rules that keep changing.

Revision ID: 9b41d6e2c7a5
Revises: 3c2078f83051
Create Date: 2026-11-14 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b41d6e2c7a5'
down_revision: Union[str, Sequence[str], None] = '3c2078f83051'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KIND = sa.Enum('duplicate', 'large_charge', 'unfamiliar_merchant', name='anomalykind')


def upgrade() -> None:
    """Add the columns detection needs; both tables were unused until now."""
    # Derived data, rebuilt by refresh_insights; clearing it lets the new NOT NULL columns go in.
    op.execute("DELETE FROM recurring_series")
    op.add_column("recurring_series", sa.Column("key", sa.String(150), nullable=False))
    op.add_column("recurring_series", sa.Column("first_date", sa.Date(), nullable=False))
    op.add_column("recurring_series", sa.Column("last_date", sa.Date(), nullable=False))
    op.add_column("recurring_series", sa.Column("charge_count", sa.Integer(), nullable=False))
    op.add_column("recurring_series", sa.Column("previous_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("recurring_series", sa.Column("amount_changed_on", sa.Date(), nullable=True))
    op.add_column("recurring_series", sa.Column("active", sa.Boolean(), nullable=False))
    op.create_unique_constraint("uq_recurring_series_user_key", "recurring_series", ["user_id", "key"])

    op.add_column("transactions", sa.Column("recurring_series_id", sa.Integer(), nullable=True))
    op.create_index("ix_transactions_recurring_series_id", "transactions", ["recurring_series_id"])
    op.create_foreign_key("fk_transactions_recurring_series_id", "transactions", "recurring_series",
                          ["recurring_series_id"], ["id"], ondelete="SET NULL")

    # No code ever wrote anomaly_flags, so it's empty and `kind` can be NOT NULL right away.
    op.add_column("anomaly_flags", sa.Column("kind", KIND, nullable=False))
    op.add_column("anomaly_flags", sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"),
                                             nullable=False))
    op.create_unique_constraint("uq_anomaly_flags_transaction_kind", "anomaly_flags", ["transaction_id", "kind"])


def downgrade() -> None:
    """Remove the detection columns."""
    # The unique constraint starts with transaction_id, so MySQL may be using it for the FK; drop the
    # constraint only after the plain index exists (it does, from the initial schema).
    op.drop_constraint("uq_anomaly_flags_transaction_kind", "anomaly_flags", type_="unique")
    op.drop_column("anomaly_flags", "created_at")
    op.drop_column("anomaly_flags", "kind")

    op.drop_constraint("fk_transactions_recurring_series_id", "transactions", type_="foreignkey")
    op.drop_index("ix_transactions_recurring_series_id", "transactions")
    op.drop_column("transactions", "recurring_series_id")

    op.drop_constraint("uq_recurring_series_user_key", "recurring_series", type_="unique")
    for col in ("active", "amount_changed_on", "previous_amount", "charge_count", "last_date", "first_date", "key"):
        op.drop_column("recurring_series", col)
