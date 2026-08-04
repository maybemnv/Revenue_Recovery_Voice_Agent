"""Bearer-token auth for the dashboard.

Two levels: an admin token that can write configs, and a viewer token that can
only read. When neither is configured the dependencies pass everything through,
which is the local development path — `main.py` logs a warning at startup so an
unauthenticated deployment announces itself rather than hiding.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from apps.api.settings import get_settings


def _token_from(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else header.strip()


def auth_disabled() -> bool:
    settings = get_settings()
    return not settings.dashboard_api_token and not settings.dashboard_viewer_token


async def require_viewer(authorization: str | None = Header(default=None)) -> str:
    """Read access. The admin token also satisfies this."""
    if auth_disabled():
        return "anonymous"
    settings = get_settings()
    token = _token_from(authorization)
    for value, role in (
        (settings.dashboard_api_token, "admin"),
        (settings.dashboard_viewer_token, "viewer"),
    ):
        if value and hmac.compare_digest(token, value):
            return role
    raise HTTPException(status_code=401, detail="invalid or missing token")


async def require_admin(authorization: str | None = Header(default=None)) -> str:
    """Write access. The viewer token is explicitly not enough."""
    if auth_disabled():
        return "anonymous"
    settings = get_settings()
    token = _token_from(authorization)
    if settings.dashboard_api_token and hmac.compare_digest(token, settings.dashboard_api_token):
        return "admin"
    raise HTTPException(status_code=403, detail="admin token required")
