from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.api_router import api_router
from src.core.config import settings
from src.core.error_handlers import register_exception_handlers

app = FastAPI(
    title="Aashish Regmi Portfolio API",
    version="1.0.0",
    description="Backend for the portfolio: contact, visitor context and admin auth.",
)

register_exception_handlers(app)
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
