"""Explicit fixture-only controls for the local sales showcase."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.db.session import get_session
from apps.api.demo.replay import get_demo_service
from apps.api.settings import get_settings

router = APIRouter(prefix="/api/demo", tags=["demo"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/reset-and-replay")
async def reset_and_replay(session: SessionDep) -> dict[str, Any]:
    settings = get_settings()
    if not settings.fixture_mode:
        raise HTTPException(status_code=404, detail="fixture replay is disabled")
    return await get_demo_service(session, client_id=settings.fixture_client_id).reset_and_replay()
