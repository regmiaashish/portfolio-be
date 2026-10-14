"""Pre-stream checks for Pitch Me, in the contract's order, then the stream itself."""

from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime

from loguru import logger

from src.core.config import settings
from src.core.exceptions import DailyCapReachedError, VerificationFailedError
from src.repositories.pitch_usage_repo import PitchUsageRepository
from src.schemas.pitch_schema import PitchQuery
from src.services.pitch.graph import PitchPipeline
from src.services.pitch.history import PitchRecorder
from src.services.pitch.stream import PitchCache, PitchRun, SSEMessage, replay
from src.services.turnstile_service import TurnstileVerifier

PipelineFactory = Callable[[], PitchPipeline]


def utc_today() -> date:
    return datetime.now(UTC).date()


class PitchService:
    def __init__(
        self,
        verifier: TurnstileVerifier,
        usage: PitchUsageRepository,
        cache: PitchCache,
        pipeline_factory: PipelineFactory,
        today: Callable[[], date] = utc_today,
        recorder: PitchRecorder | None = None,
    ):
        self.verifier = verifier
        self.usage = usage
        self.cache = cache
        self.pipeline_factory = pipeline_factory
        self.today = today
        self.recorder = recorder

    async def start(self, query: PitchQuery, client_ip: str | None) -> AsyncIterator[SSEMessage]:
        """Raises the pre-stream errors; returns the event stream once a run may go ahead."""
        if self.verifier.enabled and not await self.verifier.verify(
            query.turnstile_token, client_ip
        ):
            raise VerificationFailedError()

        cached = self.cache.get(query.company, query.role)
        if cached is not None:
            logger.info("Pitch Me run outcome=ok cached=true est_cost_usd=0")
            return replay(cached, query.company, query.role, self.recorder)

        # Counted before the run (and never refunded), so a burst can't overshoot the cap.
        runs_today = await self.usage.increment(self.today())
        if runs_today > settings.pitch_daily_cap:
            logger.warning("Pitch Me daily cap reached ({} runs)", settings.pitch_daily_cap)
            raise DailyCapReachedError()

        run = PitchRun(
            self.pipeline_factory(),
            query.company,
            query.role,
            self.cache,
            settings.pitch_timeout_seconds,
            self.recorder,
        )
        return run.stream()
