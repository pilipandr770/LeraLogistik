"""SQLAlchemy ORM models.

Design notes
------------
This schema is deliberately simple for the MVP. Key principles:

1. External proposals (from Lardi-Trans and, later, other exchanges) are
   stored in two tables: `loads` (cargo offers) and `vehicles` (free trucks).
   They use `source` + `external_id` to identify the original record.

2. `carriers` is our own registry of trucking companies we've worked with
   or vetted. External vehicles always reference the carrier they belong to
   (created on-the-fly when we first see them).

3. `matches` is produced by the AI Matcher agent. Each row is a scored
   pairing of a load with a vehicle. Humans (and, later, agents) promote
   promising matches into `negotiations`.

4. `negotiations` hold the conversational state with a carrier about a
   specific load. Messages themselves live in `negotiation_messages`.

5. `deals` is the final booked transaction. One negotiation -> zero or
   one deal.

6. All tables have `created_at` and `updated_at`. Enums are kept simple
   (strings) to avoid Postgres ENUM migration pain later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base with timestamp columns shared by all tables."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# --- Enums as string constants (not SQL ENUMs, for migration simplicity) ---

class LoadStatus:
    NEW = "new"
    MATCHED = "matched"
    NEGOTIATING = "negotiating"
    BOOKED = "booked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class VehicleStatus:
    AVAILABLE = "available"
    RESERVED = "reserved"
    BOOKED = "booked"
    EXPIRED = "expired"


class MatchStatus:
    PROPOSED = "proposed"     # AI suggested it
    REVIEWED = "reviewed"     # human saw it
    REJECTED = "rejected"     # human said no
    PROMOTED = "promoted"     # converted into a negotiation


class NegotiationStatus:
    OPEN = "open"
    AWAITING_US = "awaiting_us"
    AWAITING_THEM = "awaiting_them"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class MessageDirection:
    IN = "in"     # from carrier to us
    OUT = "out"   # from us to carrier


class MessageAuthor:
    AGENT = "agent"
    HUMAN = "human"
    CARRIER = "carrier"


# --- Core tables ---

class Carrier(Base):
    """Trucking company / individual driver we work with or have seen on the exchange."""

    __tablename__ = "carriers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    edrpou: Mapped[str | None] = mapped_column(String(32), index=True)  # Ukrainian company tax ID
    vat_number: Mapped[str | None] = mapped_column(String(32))
    country: Mapped[str | None] = mapped_column(String(2))  # ISO-3166-1 alpha-2

    # Contacts
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))

    # Lardi-Trans reference (if discovered via exchange)
    lardi_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    # Internal trust score: 0..100. Starts null, filled by Risk Agent.
    trust_score: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)

    # Relations
    vehicles: Mapped[list[Vehicle]] = relationship(back_populates="carrier")


class Load(Base):
    """A cargo proposal we might want to arrange transport for."""

    __tablename__ = "loads"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_loads_source_external"),
        Index("ix_loads_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Where it came from
    source: Mapped[str] = mapped_column(String(32), nullable=False)   # "lardi" | "email" | "manual"
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(32), default=LoadStatus.NEW, nullable=False)

    # Route
    from_country: Mapped[str | None] = mapped_column(String(2))
    from_city: Mapped[str | None] = mapped_column(String(128))
    from_postcode: Mapped[str | None] = mapped_column(String(16))
    from_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    from_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    to_country: Mapped[str | None] = mapped_column(String(2))
    to_city: Mapped[str | None] = mapped_column(String(128))
    to_postcode: Mapped[str | None] = mapped_column(String(16))
    to_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    to_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    # Cargo
    cargo_name: Mapped[str | None] = mapped_column(String(255))
    weight_tons: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    body_types: Mapped[list[str] | None] = mapped_column(JSON)  # ["tent", "refrigerator", ...]
    is_adr: Mapped[bool] = mapped_column(default=False, nullable=False)
    adr_class: Mapped[str | None] = mapped_column(String(16))

    # Dates
    pickup_date_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pickup_date_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Price hints
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_currency: Mapped[str | None] = mapped_column(String(3))  # UAH/EUR/USD
    price_is_vat_included: Mapped[bool | None] = mapped_column()

    # Owner contact (as reported by exchange)
    owner_name: Mapped[str | None] = mapped_column(String(255))
    owner_phone: Mapped[str | None] = mapped_column(String(32))
    owner_lardi_id: Mapped[int | None] = mapped_column(BigInteger)

    # Platform user who posted this load (source="platform")
    posted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)

    # Raw payload as received, for debugging and forward-compatibility
    raw_payload: Mapped[dict | None] = mapped_column(JSON)


class Vehicle(Base):
    """A free vehicle proposal we might assign to a load."""

    __tablename__ = "vehicles"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_vehicles_source_external"),
        Index("ix_vehicles_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(64), index=True)

    status: Mapped[str] = mapped_column(String(32), default=VehicleStatus.AVAILABLE, nullable=False)

    # Owner — legacy Lardi-Transport record
    carrier_id: Mapped[int | None] = mapped_column(ForeignKey("carriers.id"), index=True)
    carrier: Mapped[Carrier | None] = relationship(back_populates="vehicles")

    # Owner — platform Company (set for platform-registered vehicles)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    company: Mapped["Company | None"] = relationship("Company", foreign_keys="Vehicle.company_id", lazy="select")

    # Where it's free
    from_country: Mapped[str | None] = mapped_column(String(2))
    from_city: Mapped[str | None] = mapped_column(String(128))
    from_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    from_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))

    # Where it wants to go (optional, driver preference)
    to_country: Mapped[str | None] = mapped_column(String(2))
    to_city: Mapped[str | None] = mapped_column(String(128))

    # Capacity
    body_type: Mapped[str | None] = mapped_column(String(64))   # "tent" | "refrigerator" | ...
    capacity_tons: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    capacity_m3: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))

    # Availability window
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Our Traccar device ID (set when carrier registers the vehicle in our fleet)
    traccar_device_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Unique identifier sent by the GPS device/app (IMEI, phone, custom string)
    traccar_unique_id: Mapped[str | None] = mapped_column(String(64), index=True)

    raw_payload: Mapped[dict | None] = mapped_column(JSON)


class Match(Base):
    """AI-proposed pairing of a load with a vehicle."""

    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("load_id", "vehicle_id", name="uq_matches_load_vehicle"),
        Index("ix_matches_status_score", "status", "score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    load_id: Mapped[int] = mapped_column(ForeignKey("loads.id"), nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(32), default=MatchStatus.PROPOSED, nullable=False)

    # AI scoring (0..100) and human-readable reasoning
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text)

    # Estimated deadhead kilometres (vehicle to pickup)
    deadhead_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    suggested_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    suggested_currency: Mapped[str | None] = mapped_column(String(3))

    # Relationships
    load: Mapped["Load"] = relationship("Load", foreign_keys=[load_id], lazy="select")
    vehicle: Mapped["Vehicle"] = relationship("Vehicle", foreign_keys=[vehicle_id], lazy="select")


class Negotiation(Base):
    """A conversation with a carrier about a specific load."""

    __tablename__ = "negotiations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    load_id: Mapped[int] = mapped_column(ForeignKey("loads.id"), nullable=False, index=True)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carriers.id"), nullable=False, index=True)
    match_id: Mapped[int | None] = mapped_column(ForeignKey("matches.id"))

    status: Mapped[str] = mapped_column(String(32), default=NegotiationStatus.OPEN, nullable=False)

    # Latest offer on the table
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    current_currency: Mapped[str | None] = mapped_column(String(3))

    messages: Mapped[list[NegotiationMessage]] = relationship(
        back_populates="negotiation",
        order_by="NegotiationMessage.created_at",
        cascade="all, delete-orphan",
    )


class NegotiationMessage(Base):
    """A single message within a negotiation (inbound or outbound)."""

    __tablename__ = "negotiation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiations.id"),
        nullable=False,
        index=True,
    )
    negotiation: Mapped[Negotiation] = relationship(back_populates="messages")

    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # in | out
    author: Mapped[str] = mapped_column(String(16), nullable=False)    # agent | human | carrier
    channel: Mapped[str] = mapped_column(String(32), nullable=False)   # telegram | viber | email | phone | lardi
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # If this message was drafted by the agent and awaits human approval
    is_draft: Mapped[bool] = mapped_column(default=False, nullable=False)
    approved_by_human: Mapped[bool] = mapped_column(default=False, nullable=False)


class DealStatus:
    """Lifecycle stages of a booked deal."""
    BOOKED = "booked"          # carrier confirmed, not yet loaded
    LOADED = "loaded"          # cargo is on the truck
    IN_TRANSIT = "in_transit"  # truck is moving
    DELIVERED = "delivered"    # cargo unloaded at destination
    INVOICED = "invoiced"      # invoice sent
    PAID = "paid"              # payment received
    CANCELLED = "cancelled"    # deal fell through

    # Stages during which the shipper can see vehicle position
    TRACKABLE = frozenset([LOADED, IN_TRANSIT])


class Deal(Base):
    """A booked transaction — the end goal of the whole pipeline."""

    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    load_id: Mapped[int] = mapped_column(ForeignKey("loads.id"), nullable=False, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carriers.id"), nullable=False)
    negotiation_id: Mapped[int | None] = mapped_column(ForeignKey("negotiations.id"))

    # Agreed terms
    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    our_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # Lifecycle: booked → loaded → in_transit → delivered → invoiced → paid
    status: Mapped[str] = mapped_column(
        String(32), default=DealStatus.BOOKED, nullable=False
    )

    notes: Mapped[str | None] = mapped_column(Text)

    # Relationships (lazy-loaded by default; use selectinload in queries)
    load: Mapped["Load"] = relationship("Load", foreign_keys=[load_id], lazy="select")
    vehicle: Mapped["Vehicle"] = relationship("Vehicle", foreign_keys=[vehicle_id], lazy="select")
    carrier: Mapped["Carrier"] = relationship("Carrier", foreign_keys=[carrier_id], lazy="select")


# ---------------------------------------------------------------------------
# Platform users (3 roles) — the public marketplace / trust layer
# ---------------------------------------------------------------------------

class UserRole:
    SHIPPER = "shipper"       # заказчик грузоперевозок
    CARRIER = "carrier"       # грузоперевозчик
    FORWARDER = "forwarder"   # экспедитор
    ADMIN = "admin"           # platform admin (Lera + team)


class VerificationStatus:
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    MANUAL_REQUIRED = "manual_required"  # API unavailable, needs human review


class Company(Base):
    """Business entity — represents any company registered on the platform.

    Trust score (0–100) is built from automated checks:
      ЄДРПОУ found + active:     +35
      EU VAT (VIES) valid:       +30
      Email verified:            +15
      Phone verified:            +15
      Profile complete:           +5
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Company identity
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(512))  # auto-filled from registry
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="UA")

    # Ukrainian registry identity
    edrpou: Mapped[str | None] = mapped_column(String(16), index=True)

    # EU VAT identity
    vat_number: Mapped[str | None] = mapped_column(String(32))
    vat_country: Mapped[str | None] = mapped_column(String(2))

    # Trust / verification
    trust_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Public mini-site
    slug: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    tagline: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(String(512))
    website: Mapped[str | None] = mapped_column(String(255))

    # Contact info
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)

    # AI chatbot config (for the mini-site widget)
    chatbot_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    chatbot_system_prompt: Mapped[str | None] = mapped_column(Text)
    chatbot_greeting: Mapped[str | None] = mapped_column(String(512))

    # Role-specific data stored as JSON for flexibility:
    # carrier:   {"fleet": [...], "routes": [...], "capacity_tons": N}
    # shipper:   {"cargo_types": [...], "typical_routes": [...]}
    # forwarder: {"services": [...], "coverage_countries": [...]}
    profile_data: Mapped[dict | None] = mapped_column(JSON)

    # Relations
    users: Mapped[list["User"]] = relationship(back_populates="company")
    verification_checks: Mapped[list["VerificationCheck"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class User(Base):
    """Authenticated platform user."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[str] = mapped_column(String(32), nullable=False)  # UserRole

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Link to the business entity they represent
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), index=True)
    company: Mapped["Company | None"] = relationship(back_populates="users")


class VerificationCheck(Base):
    """Audit log of each automated verification check run for a company.

    Every run (ЄДРПОУ, VIES, phone OTP…) creates one row.
    This gives a full history including failures, which helps with support.
    """

    __tablename__ = "verification_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id"), nullable=False, index=True
    )

    check_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "edrpou" | "vies" | "phone" | "email" | "manual"

    source: Mapped[str] = mapped_column(String(64), nullable=False)
    # "opendatabot" | "vies_api" | "twilio" | "admin"

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # VerificationStatus

    score_delta: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Parsed key fields (for display without re-fetching raw)
    details: Mapped[dict | None] = mapped_column(JSON)
    # Full API response kept for audit / debugging
    raw_response: Mapped[dict | None] = mapped_column(JSON)

    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped["Company"] = relationship(back_populates="verification_checks")


# ---------------------------------------------------------------------------
# GPS / Fleet telematics
# ---------------------------------------------------------------------------

class TelematicsProvider:
    """Supported fleet management / GPS tracking providers."""
    TRACCAR = "traccar"        # Self-hosted Traccar (our own server, open-source)
    NAVIXY = "navixy"          # SaaS fallback for carriers already on Navixy
    WIALON = "wialon"          # Gurtam Wialon SaaS fallback
    MANUAL = "manual"          # Carrier updates location manually via form


class TelematicsAccount(Base):
    """A carrier's linked GPS/Fleet telematics account.

    One carrier company can link one account per provider.
    We store only the API key (encrypted at rest via app-level encryption in future).
    Never expose these keys in templates or logs.
    """

    __tablename__ = "telematics_accounts"
    __table_args__ = (
        UniqueConstraint("company_id", "provider", name="uq_telematics_company_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), nullable=False, index=True)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # TelematicsProvider
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    # Credentials (provider-specific)
    api_key: Mapped[str | None] = mapped_column(String(512))        # Navixy hash/key
    api_host: Mapped[str | None] = mapped_column(String(255))       # for self-hosted Wialon
    account_name: Mapped[str | None] = mapped_column(String(255))   # display name

    # Traccar-specific: group created on our server for this company's fleet
    traccar_group_id: Mapped[int | None] = mapped_column(Integer)

    # Last successful sync
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    # Relationships
    company: Mapped["Company"] = relationship("Company", foreign_keys=[company_id], lazy="select")


class VehiclePosition(Base):
    """GPS position snapshot for a vehicle, polled from telematics or entered manually.

    We store a time-series of positions. Only the latest row per vehicle is
    shown on the UI — older rows are kept for route history / analytics.
    """

    __tablename__ = "vehicle_positions"
    __table_args__ = (
        Index("ix_vpos_vehicle_recorded", "vehicle_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)

    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    speed_kmh: Mapped[int | None] = mapped_column(Integer)
    heading_deg: Mapped[int | None] = mapped_column(Integer)    # 0–359
    address: Mapped[str | None] = mapped_column(String(512))    # reverse-geocoded

    # Source of this position
    provider: Mapped[str] = mapped_column(String(32), nullable=False)   # TelematicsProvider
    # External ID from the provider (e.g. Navixy tracker ID)
    provider_tracker_id: Mapped[str | None] = mapped_column(String(64))

    # When the GPS fix was taken (may differ from created_at which is DB insert time)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MatchFeedback(Base):
    """Human operator rating on an AI-proposed match.

    Collected during Phase 1 (manual operations) to build a training dataset
    for future model fine-tuning and to track matcher quality over time.

    One row per (match_id, operator_id) — operator can change their mind
    by updating the existing row (UPSERT).
    """

    __tablename__ = "match_feedback"
    __table_args__ = (
        UniqueConstraint("match_id", "operator_id", name="uq_feedback_match_operator"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False, index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # "good" | "bad" | "unclear"
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    # Free-text comment (optional)
    comment: Mapped[str | None] = mapped_column(Text)


class PriceSample(Base):
    """Historical freight price data point used by the Pricing Agent.

    Populated from:
    - Closed deals on our platform (source="deal")
    - Lardi-Trans loads that have a price set (source="lardi")

    The Pricing Agent queries similar routes from the last 90 days to
    suggest a fair market price for new loads.
    """

    __tablename__ = "price_samples"
    __table_args__ = (
        Index("ix_psamples_route", "from_country", "to_country", "body_type"),
        Index("ix_psamples_collected", "collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False)   # "deal" | "lardi"
    source_id: Mapped[str | None] = mapped_column(String(64))         # deal.id or load.external_id

    from_country: Mapped[str | None] = mapped_column(String(2))
    from_city: Mapped[str | None] = mapped_column(String(128))
    to_country: Mapped[str | None] = mapped_column(String(2))
    to_city: Mapped[str | None] = mapped_column(String(128))

    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)  # UAH/EUR/USD

    weight_tons: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    body_type: Mapped[str | None] = mapped_column(String(64))
    distance_km: Mapped[int | None] = mapped_column(Integer)

    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

