"""Fix vehicles columns: add to_country/to_city, rename volume_m3→capacity_m3

Revision ID: 005_fix_vehicles_columns
Revises: 004_feedback_and_pricing
Create Date: 2026-04-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005_fix_vehicles_columns"
down_revision: Union[str, None] = "004_feedback_and_pricing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add missing columns that exist in the model but were absent from migration 001
    op.add_column("vehicles", sa.Column("to_country", sa.String(2), nullable=True))
    op.add_column("vehicles", sa.Column("to_city", sa.String(128), nullable=True))

    # Rename volume_m3 → capacity_m3 to match the model field name
    op.alter_column("vehicles", "volume_m3", new_column_name="capacity_m3")


def downgrade() -> None:
    op.alter_column("vehicles", "capacity_m3", new_column_name="volume_m3")
    op.drop_column("vehicles", "to_city")
    op.drop_column("vehicles", "to_country")
