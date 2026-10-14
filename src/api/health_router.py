import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from loguru import logger
from sqlalchemy import text

from src.core.database import engine
from src.schemas.health_schema import HealthStatus

READY_TIMEOUT_SECONDS = 2.0

DbProbe = Callable[[], Awaitable[None]]

router = APIRouter(tags=["health"])

# Probes that outlived their timeout. Kept referenced so they aren't garbage
# collected mid-flight; each removes itself when it finally finishes.
_abandoned_probes: set[asyncio.Task[None]] = set()


async def _select_one() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def get_db_probe() -> DbProbe:
    return _select_one


def _forget(task: asyncio.Task[None]) -> None:
    _abandoned_probes.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.debug("Abandoned readiness probe finished with {!r}", task.exception())


@router.get("/health", response_model=HealthStatus)
async def liveness() -> HealthStatus:
    """The process is up. Never touches the database, so it is safe for frequent probes."""
    return HealthStatus(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthStatus,
    responses={503: {"model": HealthStatus, "description": "Database unreachable"}},
)
async def readiness(probe: DbProbe = Depends(get_db_probe)) -> HealthStatus | JSONResponse:
    """The API can serve traffic: the database answers `SELECT 1` within the timeout."""
    # asyncio.wait (unlike wait_for) returns at the deadline without waiting for the
    # cancelled probe to unwind. asyncpg's cancellation needs a reply from the server,
    # so a hung database would otherwise hang this endpoint too.
    task = asyncio.create_task(probe())
    done, _ = await asyncio.wait({task}, timeout=READY_TIMEOUT_SECONDS)

    if task in done and task.exception() is None:
        return HealthStatus(status="ok")

    if task in done:
        logger.warning("Readiness check failed: {!r}", task.exception())
    else:
        logger.warning("Readiness check timed out after {}s", READY_TIMEOUT_SECONDS)
        task.cancel()
        _abandoned_probes.add(task)
        task.add_done_callback(_forget)

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=HealthStatus(status="unavailable").model_dump(),
    )
