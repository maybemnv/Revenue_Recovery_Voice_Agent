"""Process-wide settings. Every secret arrives through the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = Field(
        default="production",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"

    # datastores
    database_url: str = "postgresql+asyncpg://voice:voice@localhost:5432/voice"
    database_url_sync: str = "postgresql+psycopg://voice:voice@localhost:5432/voice"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # openai
    openai_api_key: str = ""
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime"
    openai_embedding_model: str = "text-embedding-3-small"

    # anthropic
    anthropic_api_key: str = ""
    anthropic_analysis_model: str = "claude-sonnet-5"

    # twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_messaging_from: str = ""
    twilio_validate_signatures: bool = True
    twilio_recording_enabled: bool = False

    # cal.com
    calcom_api_key: str = ""
    calcom_api_base: str = "https://api.cal.com/v2"
    calcom_event_type_id: int = 0

    # hubspot
    hubspot_access_token: str = ""
    hubspot_api_base: str = "https://api.hubapi.com"

    # stripe
    stripe_api_key: str = ""
    stripe_price_id: str = ""

    # observability
    sentry_dsn: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""

    # dashboard
    cors_allow_origins: list[str] | str = Field(default_factory=lambda: ["http://localhost:3000"])
    dashboard_api_token: str = ""
    dashboard_viewer_token: str = ""

    # Fixture mode is the only mode that exposes the deterministic demo reset route.
    fixture_mode: bool = False
    fixture_api_port: int = 8101
    fixture_web_port: int = 3101
    fixture_client_id: str = "northside-hvac"

    client_config_dir: Path = REPO_ROOT / "config" / "clients"

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _validate_runtime_boundary(self) -> Settings:
        self.app_env = self.app_env.strip().lower()
        if self.app_env not in {"local-fixture", "staging", "production"}:
            raise ValueError("APP_ENV must be local-fixture, staging, or production")
        if self.fixture_mode != (self.app_env == "local-fixture"):
            raise ValueError("FIXTURE_MODE must match APP_ENV=local-fixture")
        if self.app_env != "local-fixture":
            if not self.dashboard_api_token or not self.dashboard_viewer_token:
                raise ValueError(
                    "dashboard API and viewer tokens are required outside APP_ENV=local-fixture"
                )
            if (
                self.dashboard_api_token in {"change-me-in-production", "change-me-viewer"}
                or self.dashboard_viewer_token in {"change-me-in-production", "change-me-viewer"}
            ):
                raise ValueError(
                    "development dashboard credentials are not allowed "
                    "outside APP_ENV=local-fixture"
                )
            if not self.twilio_validate_signatures:
                raise ValueError(
                    "TWILIO_VALIDATE_SIGNATURES must be true outside APP_ENV=local-fixture"
                )
            local_values = [
                self.public_base_url,
                self.database_url,
                self.database_url_sync,
                self.redis_url,
                self.celery_broker_url,
                self.celery_result_backend,
                *self.cors_allow_origins,
            ]
            if any("localhost" in value or "127.0.0.1" in value for value in local_values):
                raise ValueError("localhost URLs are only allowed in APP_ENV=local-fixture")
        return self

    @property
    def environment(self) -> str:
        """Compatibility name for logs and integrations."""
        return self.app_env

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def websocket_base_url(self) -> str:
        """PUBLIC_BASE_URL with the scheme swapped for the WebSocket equivalent."""
        base = self.public_base_url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :]
        if base.startswith("http://"):
            return "ws://" + base[len("http://") :]
        return base


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
