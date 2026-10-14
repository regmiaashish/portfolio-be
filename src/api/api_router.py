from fastapi import APIRouter

from src.api.health_router import router as health_router
from src.api.sitemap_router import router as sitemap_router
from src.api.v1.admin_router import router as admin_router
from src.api.v1.auth_router import router as auth_router
from src.api.v1.contact_router import router as contact_router
from src.api.v1.content_router import router as content_router
from src.api.v1.context_router import router as context_router
from src.api.v1.media_router import router as media_router
from src.api.v1.pitch_router import router as pitch_router
from src.api.v1.resume_router import router as resume_router
from src.schemas.common_schema import ErrorResponse

# Documents the real error shape; this also replaces FastAPI's default 422 schema,
# which no longer matches what the validation handler returns.
_COMMON_ERRORS = {
    422: {"model": ErrorResponse, "description": "Validation error"},
    500: {"model": ErrorResponse, "description": "Internal server error"},
}

v1_router = APIRouter(prefix="/api/v1", responses=_COMMON_ERRORS)
v1_router.include_router(contact_router)
v1_router.include_router(context_router)
v1_router.include_router(pitch_router)
v1_router.include_router(content_router)
v1_router.include_router(resume_router)
v1_router.include_router(media_router)
v1_router.include_router(admin_router)
v1_router.include_router(auth_router, prefix="/auth")

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(sitemap_router)
api_router.include_router(v1_router)
