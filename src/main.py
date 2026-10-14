from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.api_router import api_router
from src.core.config import Settings, settings
from src.core.error_handlers import register_exception_handlers
from src.core.rate_limit import limiter
from src.services.context_service import ContextService
from src.services.pitch.stream import PitchCache
from src.services.storage_service import create_storage

OUTBOUND_TIMEOUT = httpx.Timeout(6.0, connect=3.0)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # One pooled HTTP client for every outbound call (geo, weather, FX, Turnstile).
    async with httpx.AsyncClient(timeout=OUTBOUND_TIMEOUT) as client:
        app.state.http_client = client
        app.state.context_service = ContextService(client)
        app.state.pitch_cache = PitchCache(settings.pitch_cache_ttl_hours)
        # S3 client for admin image uploads; None when no bucket is configured.
        app.state.storage = create_storage(settings)
        yield


def create_app(app_settings: Settings = settings) -> FastAPI:
    # Interactive docs and the schema are only exposed outside production.
    docs_enabled = not app_settings.is_prod

    app = FastAPI(
        title="Aashish Regmi Portfolio API",
        version="1.0.0",
        description=(
            "Backend for the portfolio: contact, visitor context, Pitch Me, site content and admin."
        ),
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    register_exception_handlers(app)
    app.include_router(api_router)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


app = create_app()
