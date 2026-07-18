from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from loguru import logger
from src.api.api_router import api_router
from fastapi.middleware.cors import CORSMiddleware
from src.core.config import settings

app = FastAPI()

app.include_router(api_router)

rate_limiter = Limiter(key_func=get_remote_address)  # Use remote address for rate limiting
@app.get("/")
@rate_limiter.limit("5/minute")  # Limit to 5 requests per minute
async def read_root(
    request: Request
):
    logger.warning(f"Rate limit exceeded for IP: {get_remote_address(request)}")
    logger.info(f"Gemini API key is : Hahahahahahahahah laaaazzz lagena, huh????")
    return {"message": "Hello from Aashish! Backend Engineer. Welcome to my FastAPI application!"}


# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)