"""Application configuration.

All settings are loaded from environment variables (or from a .env file
during local development). This is the single source of truth for any
configurable value in the application - do not hardcode URLs, tokens,
or flags anywhere else.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_env: Literal["development", "staging", "production"] = "development"
    app_debug: bool = True
    app_secret_key: str = "dev-secret-please-change-in-production"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # --- Database ---
    database_url: str = "postgresql+asyncpg://lera:lera@localhost:5432/lera"

    # --- Lardi-Trans API ---
    lardi_api_token: str = ""
    lardi_api_base_url: str = "https://api.lardi-trans.com/v2"
    lardi_api_language: Literal["uk", "ru", "en"] = "uk"
    lardi_poll_interval_seconds: int = 60

    # --- Anthropic Claude API ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_model_fast: str = "claude-haiku-4-5-20251001"

    # --- JWT Authentication ---
    jwt_secret_key: str = "dev-jwt-secret-please-change-in-production"
    jwt_expire_minutes: int = 60 * 24 * 30  # 30 days

    # --- Verification APIs ---
    # OpenDataBot (https://opendatabot.ua/api) — Ukrainian company registry
    opendatabot_api_key: str = ""
    # EU VIES uses no key — official free EU Commission API

    # --- GPS telematics (Traccar — self-hosted) ---
    traccar_base_url: str = "http://localhost:8082"
    traccar_admin_email: str = "admin@trucklink.ua"
    traccar_admin_password: str = ""
    traccar_poll_interval_seconds: int = 30

    # --- GPS telematics (Navixy SaaS fallback) ---
    navixy_api_base: str = "https://api.eu.navixy.com/v2"

    # --- Feature flags ---
    agent_matcher_enabled: bool = True
    agent_pricing_enabled: bool = True
    agent_negotiator_auto_send: bool = False
    agent_auto_accept_deals: bool = False

    # --- Derived properties ---
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        """Render.com supplies DATABASE_URL in the classic 'postgres://' form,
        but SQLAlchemy 2.x with asyncpg needs 'postgresql+asyncpg://'.
        """
        if not value:
            return value
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://") and "+asyncpg" not in value:
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Use this everywhere instead of instantiating Settings() directly, so
    that environment is read only once per process.
    """
    return Settings()
