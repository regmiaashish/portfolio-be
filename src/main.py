from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.api_router import api_router
from src.core.config import Settings, settings
from src.core.error_handlers import register_exception_handlers
from src.core.rate_limit import limiter


def create_app(app_settings: Settings = settings) -> FastAPI:
    # Interactive docs and the schema are only exposed outside production.
    docs_enabled = not app_settings.is_prod

    app = FastAPI(
        title="Aashish Regmi Portfolio API",
        version="1.0.0",
        description="Backend for the portfolio: contact, visitor context and admin auth.",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
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
