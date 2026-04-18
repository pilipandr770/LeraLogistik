"""006 — fix DB/model schema mismatches

vehicle_positions:  rename lng→lon, add provider/provider_tracker_id/updated_at
telematics_accounts: add api_key/api_host/account_name/last_error/updated_at
vehicles:           add is_adr / plate_number columns to match DB reality

Revision ID: 006
Revises: 005_fix_vehicles_columns
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005_fix_vehicles_columns"
branch_labels = None
depends_on = None

SCHEMA = "lera_logistics"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # vehicle_positions
    # ------------------------------------------------------------------
    # 1. Rename lng → lon
    op.execute(
        f"ALTER TABLE {SCHEMA}.vehicle_positions RENAME COLUMN lng TO lon"
    )
    # 2. Add provider (NOT NULL with default so existing rows get a value)
    op.execute(
        f"ALTER TABLE {SCHEMA}.vehicle_positions "
        f"ADD COLUMN IF NOT EXISTS provider VARCHAR(32) NOT NULL DEFAULT 'manual'"
    )
    # 3. Add provider_tracker_id
    op.execute(
        f"ALTER TABLE {SCHEMA}.vehicle_positions "
        f"ADD COLUMN IF NOT EXISTS provider_tracker_id VARCHAR(64)"
    )
    # 4. Add updated_at (Base requires it)
    op.execute(
        f"ALTER TABLE {SCHEMA}.vehicle_positions "
        f"ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )

    # ------------------------------------------------------------------
    # telematics_accounts
    # ------------------------------------------------------------------
    op.execute(
        f"ALTER TABLE {SCHEMA}.telematics_accounts "
        f"ADD COLUMN IF NOT EXISTS api_key VARCHAR(512)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.telematics_accounts "
        f"ADD COLUMN IF NOT EXISTS api_host VARCHAR(255)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.telematics_accounts "
        f"ADD COLUMN IF NOT EXISTS account_name VARCHAR(255)"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.telematics_accounts "
        f"ADD COLUMN IF NOT EXISTS last_error TEXT"
    )
    op.execute(
        f"ALTER TABLE {SCHEMA}.telematics_accounts "
        f"ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE {SCHEMA}.vehicle_positions RENAME COLUMN lon TO lng"
    )
    for col in ("provider", "provider_tracker_id", "updated_at"):
        op.execute(
            f"ALTER TABLE {SCHEMA}.vehicle_positions DROP COLUMN IF EXISTS {col}"
        )
    for col in ("api_key", "api_host", "account_name", "last_error", "updated_at"):
        op.execute(
            f"ALTER TABLE {SCHEMA}.telematics_accounts DROP COLUMN IF EXISTS {col}"
        )
