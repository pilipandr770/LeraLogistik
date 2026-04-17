"""Health check endpoint for Render.com and general monitoring."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Return ok if the DB responds, else 500."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}
