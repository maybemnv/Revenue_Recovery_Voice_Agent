"""Loads and hot-reloads `config/clients/*.yaml`.

Reload is mtime-driven and happens on the next lookup, so editing a client's
YAML (by hand or through the dashboard editor) takes effect without restarting
the media plane. Sessions already in flight keep the config they started with.
"""

from __future__ import annotations

import threading
from pathlib import Path

import yaml

from apps.api.config.schema import ClientConfig
from apps.api.settings import get_settings


class ClientConfigNotFound(LookupError):
    pass


class ClientConfigRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or get_settings().client_config_dir
        self._lock = threading.RLock()
        self._by_id: dict[str, ClientConfig] = {}
        self._by_number: dict[str, ClientConfig] = {}
        self._mtimes: dict[Path, float] = {}
        self._fixture_overrides: dict[str, ClientConfig] = {}

    # -- loading ----------------------------------------------------------
    def _yaml_paths(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(p for p in self._dir.iterdir() if p.suffix in (".yaml", ".yml"))

    def _stale(self) -> bool:
        paths = self._yaml_paths()
        if {p: p.stat().st_mtime for p in paths} != self._mtimes:
            return True
        return not self._by_id and bool(paths)

    def reload(self) -> None:
        with self._lock:
            by_id: dict[str, ClientConfig] = {}
            by_number: dict[str, ClientConfig] = {}
            mtimes: dict[Path, float] = {}
            for path in self._yaml_paths():
                cfg = load_client_config(path)
                if cfg.client_id in by_id:
                    raise ValueError(f"duplicate client_id {cfg.client_id!r} in {path}")
                if cfg.phone_number in by_number:
                    raise ValueError(f"duplicate phone_number {cfg.phone_number!r} in {path}")
                by_id[cfg.client_id] = cfg
                by_number[cfg.phone_number] = cfg
                mtimes[path] = path.stat().st_mtime
            self._by_id, self._by_number, self._mtimes = by_id, by_number, mtimes

    def _ensure_fresh(self) -> None:
        if self._stale():
            self.reload()

    # -- lookup -----------------------------------------------------------
    def get(self, client_id: str) -> ClientConfig:
        self._ensure_fresh()
        try:
            return self._fixture_overrides.get(client_id, self._by_id[client_id])
        except KeyError as exc:
            raise ClientConfigNotFound(f"no config for client_id {client_id!r}") from exc

    def resolve_by_number(self, e164: str) -> ClientConfig:
        """Twilio's `To` number is the only routing key we get on an inbound call."""
        self._ensure_fresh()
        try:
            config = self._by_number[e164]
            return self._fixture_overrides.get(config.client_id, config)
        except KeyError as exc:
            raise ClientConfigNotFound(f"no client bound to number {e164!r}") from exc

    def all(self) -> list[ClientConfig]:
        self._ensure_fresh()
        return [
            self._fixture_overrides.get(client_id, config)
            for client_id, config in self._by_id.items()
        ]

    def replace_fixture_config(self, client_id: str, payload: object) -> ClientConfig:
        """Replace one fixture config in memory without changing its routing identity."""
        with self._lock:
            self._ensure_fresh()
            try:
                original = self._by_id[client_id]
            except KeyError as exc:
                raise ClientConfigNotFound(f"no config for client_id {client_id!r}") from exc
            config = ClientConfig.model_validate(payload)
            if config.client_id != original.client_id:
                raise ValueError("fixture config client_id must match the route client")
            if config.phone_number != original.phone_number:
                raise ValueError("fixture config phone_number cannot change routing")
            self._fixture_overrides[client_id] = config
            return config

    def reset_fixture_config(self, client_id: str) -> None:
        with self._lock:
            self._fixture_overrides.pop(client_id, None)


def load_client_config(path: Path) -> ClientConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return ClientConfig.model_validate(data)


def dump_client_config(cfg: ClientConfig, path: Path) -> None:
    """Write a validated config back to YAML (used by the dashboard editor)."""
    payload = cfg.model_dump(mode="json", exclude_none=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


_registry: ClientConfigRegistry | None = None


def get_registry() -> ClientConfigRegistry:
    global _registry
    if _registry is None:
        _registry = ClientConfigRegistry()
    return _registry
