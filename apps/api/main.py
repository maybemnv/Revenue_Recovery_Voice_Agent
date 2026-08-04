"""FastAPI application wiring.

Startup does three things and then gets out of the way: configure logging,
validate every client config, and say out loud which safety controls are
currently off. Failing fast on a malformed YAML at boot is the difference
between finding it now and finding it when a customer calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api.config.loader import get_registry
from apps.api.media import gateway
from apps.api.observability.logging import configure_logging, get_logger
from apps.api.routers import dashboard, health, stream
from apps.api.routers.auth import auth_disabled
from apps.api.settings import get_settings
from apps.api.telephony import status_webhook, twiml

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.is_production)

    # A bad client YAML must break the boot, not the first call that hits it.
    configs = get_registry().all()
    log.info(
        "startup",
        environment=settings.environment,
        clients=[c.client_id for c in configs],
        public_base_url=settings.public_base_url,
    )

    if not settings.twilio_validate_signatures:
        log.warning("twilio_signature_validation_disabled")
    if auth_disabled():
        log.warning("dashboard_auth_disabled")
    if not configs:
        log.warning("no_client_configs_loaded", directory=str(settings.client_config_dir))

    _init_sentry(settings.sentry_dsn, settings.environment)
    yield
    log.info("shutdown")


def _init_sentry(dsn: str, environment: str) -> None:
    if not dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            traces_sample_rate=0.1,
            # PII is redacted before it reaches a log line; this stops the SDK
            # putting it back via request bodies and headers.
            send_default_pii=False,
        )
        log.info("sentry_initialised", environment=environment)
    except ImportError:
        log.warning("sentry_dsn_set_but_sdk_missing")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Revenue Recovery Voice Agent", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(twiml.router)
    app.include_router(status_webhook.router)
    app.include_router(gateway.router)
    app.include_router(dashboard.router)
    app.include_router(stream.router)
    return app


app = create_app()
