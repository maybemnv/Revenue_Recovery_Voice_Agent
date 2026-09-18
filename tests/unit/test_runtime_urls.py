"""Production host rejection and explicit fixture opt-in contracts."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from dotenv import dotenv_values

from apps.api.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
URL_FIELDS = (
    "public_base_url", "database_url", "database_url_sync", "redis_url",
    "celery_broker_url", "celery_result_backend", "cors_allow_origins",
)


def production_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "APP_ENV": "production", "fixture_mode": False,
        "dashboard_api_token": "test-admin", "dashboard_viewer_token": "test-viewer",
        "twilio_validate_signatures": True,
        "public_base_url": "https://voice.example",
        "database_url": "postgresql+asyncpg://voice@postgres/voice",
        "database_url_sync": "postgresql+psycopg://voice@postgres/voice",
        "redis_url": "redis://redis/0", "celery_broker_url": "redis://redis/1",
        "celery_result_backend": "redis://redis/2",
        "cors_allow_origins": ["https://dashboard.example"],
    }
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize("field", URL_FIELDS)
@pytest.mark.parametrize("host", [
    "[::1]", "[0:0:0:0:0:0:0:1]", "[::]", "0.0.0.0", "127.4.3.2",
    "[::ffff:127.0.0.1]", "[::ffff:0.0.0.0]", "LOCALHOST.", "api.localhost",
    "127.1", "2130706433", "0x7f000001", "0177.0.0.1",
])
def test_production_rejects_local_hosts(field: str, host: str) -> None:
    scheme = "postgresql+asyncpg" if field.startswith("database") else "http"
    url = f"{scheme}://{host}:8000/service"
    with pytest.raises(ValueError, match="only allowed in APP_ENV=local-fixture"):
        production_settings(**{field: [url] if field == "cors_allow_origins" else url})


@pytest.mark.parametrize("url", ["", "redis", "http:///missing-host", "http://[broken"])
def test_production_rejects_missing_or_malformed_hosts(url: str) -> None:
    with pytest.raises(ValueError):
        production_settings(redis_url=url)


def test_production_allows_private_networks_and_checks_host_only() -> None:
    settings = production_settings(
        database_url="postgresql+asyncpg://voice:localhost@10.0.0.2/voice",
        redis_url="redis://[fd00::2]/0",
        public_base_url="https://voice.example/localhost",
    )
    assert settings.is_production


def test_example_requires_auth_but_explicit_fixture_profile_is_secret_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in dotenv_values(ROOT / ".env.example").items():
        monkeypatch.setenv(key, value or "")
    with pytest.raises(ValueError, match="dashboard API and viewer tokens"):
        Settings(_env_file=None)

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for key, value in compose["services"]["api-fixture"]["environment"].items():
        monkeypatch.setenv(key, str(value))
    assert Settings(_env_file=None).fixture_mode
