from fastapi import APIRouter
from src.api.v1.contact_router import router as contact_router
from src.api.v1.auth_router import auth_router

api_router = APIRouter()
api_router.include_router(contact_router)
api_router.include_router(auth_router, prefix="/auth", tags=["auth"])


