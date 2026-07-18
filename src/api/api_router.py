from fastapi import APIRouter
from src.api.v1.contact_router import router as contact_router

api_router = APIRouter()
api_router.include_router(contact_router)


