"""Liveness and readiness.

`/health` is the cheap process-only probe for orchestrators that need to
distinguish "restart me" from "do not send me traffic yet". `/health/ready`
checks the dependencies needed to accept traffic.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from apps.api.db.session import get_engine, get_sessionmaker
from apps.api.demo.replay import SqlAlchemyFixtureReplayRepository
from apps.api.observability.live import get_hub
from apps.api.settings import get_settings

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, Any]:
    return await liveness()


@router.get("/health/live")
async def liveness() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "live_subscribers": get_hub().subscriber_count,
    }


async def _check_postgres() -> tuple[bool, str | None]:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # the reason is the payload
        return False, type(exc).__name__


async def _check_redis() -> tuple[bool, str | None]:
    try:
        import redis.asyncio as redis

        client = redis.from_url(get_settings().redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return True, None
    except Exception as exc:
        return False, type(exc).__name__


async def _fixture_data_ready(*, client_id: str) -> bool:
    """Read fixture readiness behind a replaceable session seam for route tests."""
    async with get_sessionmaker()() as session:
        repository = SqlAlchemyFixtureReplayRepository(session)
        return await repository.fixture_data_ready(client_id=client_id)


@router.get("/health/ready")
async def ready(response: Response) -> dict[str, Any]:
    return await _readiness(response)


async def _readiness(response: Response) -> dict[str, Any]:
    postgres_ok, postgres_error = await _check_postgres()
    redis_ok, redis_error = await _check_redis()

    settings = get_settings()
    fixture_ready = None
    if settings.fixture_mode and postgres_ok:
        fixture_ready = await _fixture_data_ready(client_id=settings.fixture_client_id)
    checks = {
        "api": {"ok": True},
        "postgres": {"ok": postgres_ok, "error": postgres_error},
        "redis": {"ok": redis_ok, "error": redis_error},
    }
    if settings.fixture_mode:
        checks["fixture_data"] = {"ok": fixture_ready}
    ready_now = postgres_ok and redis_ok and (fixture_ready is not False)
    if not ready_now:
        response.status_code = 503
    result = {
        "status": "ready" if ready_now else "degraded",
        "fixture": settings.fixture_mode,
        "simulated": settings.fixture_mode,
        "checks": checks,
    }
    if settings.fixture_mode:
        result["fixture_client_id"] = settings.fixture_client_id
    return result
