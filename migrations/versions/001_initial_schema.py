"""initial schema — carriers, loads, vehicles, matches, negotiations, deals,
companies, users, verification_checks

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-04-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── carriers ────────────────────────────────────────────────────────────
    op.create_table(
        "carriers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("edrpou", sa.String(32), nullable=True),
        sa.Column("vat_number", sa.String(32), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("lardi_user_id", sa.BigInteger(), nullable=True),
        sa.Column("trust_score", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_carriers_edrpou", "carriers", ["edrpou"])
    op.create_index("ix_carriers_lardi_user_id", "carriers", ["lardi_user_id"])

    # ── loads ────────────────────────────────────────────────────────────────
    op.create_table(
        "loads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("from_country", sa.String(2), nullable=True),
        sa.Column("from_city", sa.String(128), nullable=True),
        sa.Column("from_postcode", sa.String(16), nullable=True),
        sa.Column("from_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("from_lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("to_country", sa.String(2), nullable=True),
        sa.Column("to_city", sa.String(128), nullable=True),
        sa.Column("to_postcode", sa.String(16), nullable=True),
        sa.Column("to_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("to_lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("cargo_name", sa.String(255), nullable=True),
        sa.Column("weight_tons", sa.Numeric(8, 3), nullable=True),
        sa.Column("volume_m3", sa.Numeric(8, 3), nullable=True),
        sa.Column("body_types", sa.JSON(), nullable=True),
        sa.Column("is_adr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("adr_class", sa.String(16), nullable=True),
        sa.Column("pickup_date_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pickup_date_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_currency", sa.String(3), nullable=True),
        sa.Column("price_is_vat_included", sa.Boolean(), nullable=True),
        sa.Column("owner_name", sa.String(255), nullable=True),
        sa.Column("owner_phone", sa.String(32), nullable=True),
        sa.Column("owner_lardi_id", sa.BigInteger(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_loads_source_external"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_loads_external_id", "loads", ["external_id"])
    op.create_index("ix_loads_status_created", "loads", ["status", "created_at"])

    # ── vehicles ─────────────────────────────────────────────────────────────
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("carrier_id", sa.Integer(), sa.ForeignKey("carriers.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="available"),
        sa.Column("from_country", sa.String(2), nullable=True),
        sa.Column("from_city", sa.String(128), nullable=True),
        sa.Column("from_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("from_lon", sa.Numeric(9, 6), nullable=True),
        sa.Column("body_type", sa.String(64), nullable=True),
        sa.Column("capacity_tons", sa.Numeric(8, 3), nullable=True),
        sa.Column("volume_m3", sa.Numeric(8, 3), nullable=True),
        sa.Column("is_adr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plate_number", sa.String(32), nullable=True),
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_vehicles_source_external"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicles_carrier_id", "vehicles", ["carrier_id"])
    op.create_index("ix_vehicles_external_id", "vehicles", ["external_id"])
    op.create_index("ix_vehicles_status", "vehicles", ["status"])

    # ── matches ───────────────────────────────────────────────────────────────
    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("load_id", sa.Integer(), sa.ForeignKey("loads.id"), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("deadhead_km", sa.Numeric(8, 1), nullable=True),
        sa.Column("suggested_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("suggested_currency", sa.String(3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("load_id", "vehicle_id", name="uq_matches_load_vehicle"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_matches_load_id", "matches", ["load_id"])
    op.create_index("ix_matches_vehicle_id", "matches", ["vehicle_id"])
    op.create_index("ix_matches_status_score", "matches", ["status", "score"])

    # ── negotiations ──────────────────────────────────────────────────────────
    op.create_table(
        "negotiations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("load_id", sa.Integer(), sa.ForeignKey("loads.id"), nullable=False),
        sa.Column("carrier_id", sa.Integer(), sa.ForeignKey("carriers.id"), nullable=False),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id"), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("current_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("current_currency", sa.String(3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_negotiations_load_id", "negotiations", ["load_id"])
    op.create_index("ix_negotiations_carrier_id", "negotiations", ["carrier_id"])

    # ── negotiation_messages ──────────────────────────────────────────────────
    op.create_table(
        "negotiation_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("negotiation_id", sa.Integer(), sa.ForeignKey("negotiations.id"), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("author", sa.String(16), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("approved_by_human", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_negotiation_messages_negotiation_id", "negotiation_messages", ["negotiation_id"])

    # ── deals ─────────────────────────────────────────────────────────────────
    op.create_table(
        "deals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("load_id", sa.Integer(), sa.ForeignKey("loads.id"), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id"), nullable=False),
        sa.Column("carrier_id", sa.Integer(), sa.ForeignKey("carriers.id"), nullable=False),
        sa.Column("negotiation_id", sa.Integer(), sa.ForeignKey("negotiations.id"), nullable=True),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_currency", sa.String(3), nullable=False),
        sa.Column("our_margin", sa.Numeric(12, 2), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="booked"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deals_load_id", "deals", ["load_id"])

    # ── companies ─────────────────────────────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("legal_name", sa.String(512), nullable=True),
        sa.Column("country", sa.String(2), nullable=False, server_default="UA"),
        sa.Column("edrpou", sa.String(16), nullable=True),
        sa.Column("vat_number", sa.String(32), nullable=True),
        sa.Column("vat_country", sa.String(2), nullable=True),
        sa.Column("trust_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slug", sa.String(128), nullable=True, unique=True),
        sa.Column("tagline", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("logo_url", sa.String(512), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("chatbot_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("chatbot_system_prompt", sa.Text(), nullable=True),
        sa.Column("chatbot_greeting", sa.String(512), nullable=True),
        sa.Column("profile_data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_companies_edrpou", "companies", ["edrpou"])
    op.create_index("ix_companies_slug", "companies", ["slug"], unique=True)

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_company_id", "users", ["company_id"])

    # ── verification_checks ───────────────────────────────────────────────────
    op.create_table(
        "verification_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("check_type", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("score_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_verification_checks_company_id", "verification_checks", ["company_id"])


def downgrade() -> None:
    op.drop_table("verification_checks")
    op.drop_table("users")
    op.drop_table("companies")
    op.drop_table("deals")
    op.drop_table("negotiation_messages")
    op.drop_table("negotiations")
    op.drop_table("matches")
    op.drop_table("vehicles")
    op.drop_table("loads")
    op.drop_table("carriers")
