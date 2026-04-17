"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routes import dashboard, health, loads_routes
from app.routes import auth as auth_routes
from app.routes import onboarding as onboarding_routes
from app.routes import profile as profile_routes
from app.routes import chatbot as chatbot_routes
from app.routes import loads_platform
from app.routes import search as search_routes
from app.routes import tracking as tracking_routes
from app.routes import admin as admin_routes
from app.routes import deals as deals_routes
from app.routes import fleet as fleet_routes
from app.services.gps_poll import init_gps_poll_service, run_gps_poll_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

log = logging.getLogger(__name__)


async def _lardi_ingest_job() -> None:
    """Periodic task: pull fresh loads + vehicles from Lardi-Trans and run AI matcher.

    This runs every N seconds (configured via LARDI_POLL_INTERVAL_SECONDS).
    Runs only when LARDI_API_TOKEN is configured — silently skips otherwise.
    """
    settings = get_settings()
    if not settings.lardi_api_token:
        log.debug("Lardi ingest skipped: LARDI_API_TOKEN not set")
        return

    try:
        from app.adapters.lardi import LardiAdapter
        from app.adapters.base import SearchFilter
        from app.db.session import AsyncSessionLocal
        from app.services.ingestion import IngestionService

        flt = SearchFilter(countries_from=["UA"])
        async with AsyncSessionLocal() as session:
            async with LardiAdapter() as adapter:
                svc = IngestionService(adapter, session)
                new_loads = await svc.ingest_loads(flt)
                new_vehicles = await svc.ingest_vehicles(flt)
                if new_loads or new_vehicles:
                    log.info(
                        "Lardi ingest: +%d loads, +%d vehicles", new_loads, new_vehicles
                    )
    except Exception:
        log.exception("Lardi ingest job failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start APScheduler on startup, stop it on shutdown."""
    settings = get_settings()
    log.info("Starting TruckLink (env=%s, debug=%s)", settings.app_env, settings.app_debug)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _lardi_ingest_job,
        trigger="interval",
        seconds=settings.lardi_poll_interval_seconds,
        id="lardi_ingest",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    # GPS poll — only activates when TRACCAR_ADMIN_PASSWORD is set
    if settings.traccar_admin_password:
        init_gps_poll_service(
            settings.traccar_base_url,
            settings.traccar_admin_email,
            settings.traccar_admin_password,
        )
        scheduler.add_job(
            run_gps_poll_job,
            trigger="interval",
            seconds=settings.traccar_poll_interval_seconds,
            id="gps_poll",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=10,
        )
        log.info(
            "GPS poll job scheduled — Traccar %s every %ds",
            settings.traccar_base_url,
            settings.traccar_poll_interval_seconds,
        )
    else:
        log.info("GPS poll disabled — set TRACCAR_ADMIN_PASSWORD to enable")

    scheduler.start()
    log.info(
        "APScheduler started — Lardi ingest every %ds", settings.lardi_poll_interval_seconds
    )

    yield

    scheduler.shutdown(wait=False)
    log.info("APScheduler stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Lera Logistics",
        version="0.1.0",
        description="AI-powered logistics brokerage MVP",
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(onboarding_routes.router)
    app.include_router(profile_routes.router)
    app.include_router(chatbot_routes.router)
    app.include_router(loads_platform.router)
    app.include_router(search_routes.router)
    app.include_router(tracking_routes.router)
    app.include_router(admin_routes.router)
    app.include_router(deals_routes.router)
    app.include_router(fleet_routes.router)
    app.include_router(dashboard.router)
    app.include_router(loads_routes.router)

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc: Exception):
        log.exception("Unhandled exception: %s", exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
