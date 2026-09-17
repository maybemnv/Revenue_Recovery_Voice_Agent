"""Fixture-only dashboard configuration contracts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.config.loader import ClientConfigRegistry, dump_client_config
from apps.api.config.schema import ClientConfig
from apps.api.routers import auth, dashboard
from apps.api.settings import Settings


@pytest.fixture
def fixture_registry(tmp_path: Any, client_config_dict: dict[str, Any]) -> ClientConfigRegistry:
    dump_client_config(ClientConfig.model_validate(client_config_dict), tmp_path / "demo-hvac.yaml")
    return ClientConfigRegistry(tmp_path)


@pytest.fixture
def dashboard_client(
    monkeypatch: pytest.MonkeyPatch, fixture_registry: ClientConfigRegistry
) -> TestClient:
    settings = SimpleNamespace(
        fixture_mode=True,
        dashboard_api_token="fixture-admin",
        dashboard_viewer_token="fixture-viewer",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    monkeypatch.setattr(dashboard, "get_registry", lambda: fixture_registry)
    app = FastAPI()
    app.include_router(dashboard.router)
    with TestClient(app) as client:
        yield client


def test_fixture_admin_save_updates_next_config_snapshot_without_rewriting_yaml(
    dashboard_client: TestClient,
    fixture_registry: ClientConfigRegistry,
    client_config_dict: dict[str, Any],
) -> None:
    """Replacing a fixture config must affect only the next resolved call snapshot."""
    active_call_snapshot = fixture_registry.get("demo-hvac")
    payload = {**client_config_dict, "display_name": "Fixture HVAC"}

    response = dashboard_client.put(
        "/api/fixture/clients/demo-hvac/config",
        headers={"Authorization": "Bearer fixture-admin"},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Fixture HVAC"
    assert fixture_registry.get("demo-hvac").display_name == "Fixture HVAC"
    assert active_call_snapshot.display_name == "Demo HVAC"


def test_fixture_config_write_rejects_viewers_and_invalid_or_mismatched_configs(
    dashboard_client: TestClient, client_config_dict: dict[str, Any]
) -> None:
    """Removing role, schema, or routing guards must break this fixture boundary."""
    viewer = dashboard_client.put(
        "/api/fixture/clients/demo-hvac/config",
        headers={"Authorization": "Bearer fixture-viewer"},
        json=client_config_dict,
    )
    invalid = dashboard_client.put(
        "/api/fixture/clients/demo-hvac/config",
        headers={"Authorization": "Bearer fixture-admin"},
        json={**client_config_dict, "phone_number": "not-a-number"},
    )
    mismatched = dashboard_client.put(
        "/api/fixture/clients/demo-hvac/config",
        headers={"Authorization": "Bearer fixture-admin"},
        json={**client_config_dict, "client_id": "another-client"},
    )

    assert viewer.status_code == 403
    assert invalid.status_code == 422
    assert mismatched.status_code == 409


def test_fixture_reset_removes_only_the_selected_runtime_override(
    fixture_registry: ClientConfigRegistry, client_config_dict: dict[str, Any]
) -> None:
    """A replay must restore the fixture default without changing its number routing."""
    fixture_registry.replace_fixture_config(
        "demo-hvac", {**client_config_dict, "display_name": "Changed for this replay"}
    )

    fixture_registry.reset_fixture_config("demo-hvac")

    assert fixture_registry.get("demo-hvac").display_name == "Demo HVAC"
    assert fixture_registry.resolve_by_number("+13125550123").client_id == "demo-hvac"


def test_tokenless_fixture_roles_still_enforce_viewer_read_only(
    monkeypatch: pytest.MonkeyPatch,
    fixture_registry: ClientConfigRegistry,
    client_config_dict: dict[str, Any],
) -> None:
    """The fixture compose default has no production tokens, but viewer cannot write."""
    settings = SimpleNamespace(fixture_mode=True, dashboard_api_token="", dashboard_viewer_token="")
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(dashboard, "get_settings", lambda: settings)
    monkeypatch.setattr(dashboard, "get_registry", lambda: fixture_registry)
    app = FastAPI()
    app.include_router(dashboard.router)
    with TestClient(app) as client:
        viewer = client.put(
            "/api/fixture/clients/demo-hvac/config",
            headers={"X-Fixture-Role": "viewer"},
            json=client_config_dict,
        )
        admin = client.put(
            "/api/fixture/clients/demo-hvac/config",
            headers={"X-Fixture-Role": "admin"},
            json={**client_config_dict, "display_name": "Fixture admin change"},
        )

    assert viewer.status_code == 403
    assert admin.status_code == 200


def test_fixture_mode_requires_local_fixture_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("FIXTURE_MODE", "true")

    with pytest.raises(ValueError, match="APP_ENV=local-fixture"):
        Settings()


def test_non_fixture_settings_require_server_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("FIXTURE_MODE", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://voice.staging.example")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://voice@db.internal/voice")
    monkeypatch.setenv("REDIS_URL", "rediss://redis.internal/0")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://dashboard.staging.example")
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "")
    monkeypatch.setenv("DASHBOARD_VIEWER_TOKEN", "")

    with pytest.raises(ValueError, match="dashboard API and viewer tokens"):
        Settings()


def test_non_fixture_settings_reject_local_urls_and_disabled_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("FIXTURE_MODE", "false")
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "staging-admin")
    monkeypatch.setenv("DASHBOARD_VIEWER_TOKEN", "staging-viewer")
    monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURES", "false")

    with pytest.raises(ValueError, match="TWILIO_VALIDATE_SIGNATURES"):
        Settings()

    monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURES", "true")
    with pytest.raises(ValueError, match="localhost URLs"):
        Settings()
