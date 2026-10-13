from fastapi import APIRouter

from src.api.v1.auth_router import auth_router
from src.api.v1.contact_router import router as contact_router
from src.api.v1.context_router import router as context_router

api_router = APIRouter()
api_router.include_router(contact_router)
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(context_router)
