"""Add match_feedback and price_samples tables

Revision ID: 004_feedback_and_pricing
Revises: 003_traccar_device_fields
Create Date: 2026-05-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_feedback_and_pricing"
down_revision: Union[str, None] = "003_traccar_device_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── match_feedback ─────────────────────────────────────────────────────
    op.create_table(
        "match_feedback",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"]),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "operator_id", name="uq_feedback_match_operator"),
    )
    op.create_index("ix_feedback_match_id", "match_feedback", ["match_id"])
    op.create_index("ix_feedback_operator_id", "match_feedback", ["operator_id"])

    # ── price_samples ──────────────────────────────────────────────────────
    op.create_table(
        "price_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("from_country", sa.String(length=2), nullable=True),
        sa.Column("from_city", sa.String(length=128), nullable=True),
        sa.Column("to_country", sa.String(length=2), nullable=True),
        sa.Column("to_city", sa.String(length=128), nullable=True),
        sa.Column("price_amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("price_currency", sa.String(length=3), nullable=False),
        sa.Column("weight_tons", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("body_type", sa.String(length=64), nullable=True),
        sa.Column("distance_km", sa.Integer(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_psamples_route", "price_samples", ["from_country", "to_country", "body_type"])
    op.create_index("ix_psamples_collected", "price_samples", ["collected_at"])


def downgrade() -> None:
    op.drop_index("ix_psamples_collected", table_name="price_samples")
    op.drop_index("ix_psamples_route", table_name="price_samples")
    op.drop_table("price_samples")

    op.drop_index("ix_feedback_operator_id", table_name="match_feedback")
    op.drop_index("ix_feedback_match_id", table_name="match_feedback")
    op.drop_table("match_feedback")
