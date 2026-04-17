"""Add Traccar device fields to vehicles and telematics_accounts

Revision ID: 003_traccar_device_fields
Revises: 002_gps_and_platform_loads
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_traccar_device_fields"
down_revision: Union[str, None] = "002_gps_and_platform_loads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── vehicles: platform company owner ──────────────────────────────────
    op.add_column(
        "vehicles",
        sa.Column("company_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_vehicles_company_id",
        "vehicles",
        "companies",
        ["company_id"],
        ["id"],
    )
    op.create_index(
        "ix_vehicles_company_id",
        "vehicles",
        ["company_id"],
        unique=False,
    )

    # ── vehicles: Traccar device link ──────────────────────────────────────
    op.add_column(
        "vehicles",
        sa.Column("traccar_device_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vehicles",
        sa.Column("traccar_unique_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_vehicles_traccar_device_id",
        "vehicles",
        ["traccar_device_id"],
        unique=False,
    )
    op.create_index(
        "ix_vehicles_traccar_unique_id",
        "vehicles",
        ["traccar_unique_id"],
        unique=False,
    )

    # ── telematics_accounts: Traccar group link ────────────────────────────
    op.add_column(
        "telematics_accounts",
        sa.Column("traccar_group_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telematics_accounts", "traccar_group_id")

    op.drop_index("ix_vehicles_traccar_unique_id", table_name="vehicles")
    op.drop_index("ix_vehicles_traccar_device_id", table_name="vehicles")
    op.drop_column("vehicles", "traccar_unique_id")
    op.drop_column("vehicles", "traccar_device_id")

    op.drop_constraint("fk_vehicles_company_id", "vehicles", type_="foreignkey")
    op.drop_index("ix_vehicles_company_id", table_name="vehicles")
    op.drop_column("vehicles", "company_id")
