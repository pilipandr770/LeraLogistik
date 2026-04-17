"""Add GPS telematics tables and posted_by_user_id on loads

Revision ID: 002_gps_and_platform_loads
Revises: 001_initial_schema
Create Date: 2026-04-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002_gps_and_platform_loads"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── loads.posted_by_user_id FK ─────────────────────────────────────────
    op.add_column(
        "loads",
        sa.Column("posted_by_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_loads_posted_by_user_id",
        "loads",
        "users",
        ["posted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_loads_posted_by_user_id", "loads", ["posted_by_user_id"])

    # ── telematics_accounts ────────────────────────────────────────────────
    op.create_table(
        "telematics_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum("navixy", "wialon", "manual", name="telematicsprovider"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("external_account_id", sa.String(128), nullable=True),
        sa.Column("session_hash", sa.Text(), nullable=True),
        sa.Column("login_encrypted", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telematics_accounts_company_id", "telematics_accounts", ["company_id"]
    )

    # ── vehicle_positions ──────────────────────────────────────────────────
    op.create_table(
        "vehicle_positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("telematics_account_id", sa.Integer(), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("speed_kmh", sa.Numeric(6, 1), nullable=True),
        sa.Column("heading_deg", sa.SmallInteger(), nullable=True),
        sa.Column("satellite_count", sa.SmallInteger(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telematics_account_id"],
            ["telematics_accounts.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vehicle_positions_vehicle_id", "vehicle_positions", ["vehicle_id"]
    )
    op.create_index(
        "ix_vehicle_positions_recorded_at",
        "vehicle_positions",
        ["vehicle_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_table("vehicle_positions")
    op.drop_table("telematics_accounts")
    op.drop_constraint("fk_loads_posted_by_user_id", "loads", type_="foreignkey")
    op.drop_index("ix_loads_posted_by_user_id", table_name="loads")
    op.drop_column("loads", "posted_by_user_id")
    # Drop enum type created by Alembic
    op.execute("DROP TYPE IF EXISTS telematicsprovider")
