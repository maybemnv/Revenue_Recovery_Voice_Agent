"""Environment for the rig. Every credential arrives here, never in a YAML file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

RIG_ROOT = Path(__file__).resolve().parents[1]
PROSPECTS_DIR = RIG_ROOT / "prospects"
TEMPLATES_DIR = RIG_ROOT / "templates"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(RIG_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # Claude - powers the website -> PracticeProfile extraction only.
    anthropic_api_key: str = ""
    extraction_model: str = "claude-opus-4-8"

    # Retell - the voice platform. Agent, KB, and number provisioning.
    retell_api_key: str = ""
    retell_api_base: str = "https://api.retellai.com"
    retell_voice_id: str = "11labs-Adrian"
    retell_llm_model: str = "gpt-4o"
    retell_area_code: int = 312
    # Where a red-flag transfer lands. A voicemail box the rep controls, so the
    # prospect hears the handoff without a real phone ringing.
    retell_transfer_number: str = ""

    # Supabase - the demo calendar and call log.
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Where the mock tool webhooks are reachable from Retell.
    webhook_base_url: str = "http://localhost:8000"

    # Vercel - one demo page deploy per prospect.
    vercel_token: str = ""
    vercel_project: str = "dental-demo"
    vercel_org_id: str = ""

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def retell_configured(self) -> bool:
        return bool(self.retell_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
