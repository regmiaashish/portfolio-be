"""Turns a pipeline run (or a cached one) into Server-Sent Events.

The pipeline runs in its own task that feeds a queue. The SSE generator reads from the queue
with a deadline, so a slow provider can't hold the stream past PITCH_TIMEOUT_SECONDS, and if
the visitor disconnects the generator is closed and the task is cancelled at once: no more
paid calls for a reader who left.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from cachetools import TTLCache
from loguru import logger

from src.models.pitch_run_model import PitchOutcome
from src.services.pitch.graph import PitchEvent, PitchPipeline
from src.services.pitch.history import PitchRecorder, PitchRunResult
from src.services.pitch.llm import PitchUpstreamError

# Estimates for the cost log line only (USD, provider list prices at the time of writing).
EST_USD_PER_1M_INPUT_TOKENS = 0.30
EST_USD_PER_1M_OUTPUT_TOKENS = 2.50
EST_USD_PER_SEARCH = 0.008

REPLAY_TOKEN_DELAY_SECONDS = 0.01

MESSAGES = {
    "timeout": "The pitch took too long to write. Please try again.",
    "upstream": "One of the AI services didn't respond. Please try again in a minute.",
    "internal": "Something went wrong while writing your pitch. Please try again.",
}

# Internal outcome names (used in the log line) -> what Pitch history stores.
OUTCOMES = {
    "ok": PitchOutcome.COMPLETED,
    "timeout": PitchOutcome.TIMEOUT,
    "upstream": PitchOutcome.UPSTREAM_ERROR,
    "internal": PitchOutcome.INTERNAL_ERROR,
    "disconnected": PitchOutcome.DISCONNECTED,
}
ERROR_CODES = {"timeout": "timeout", "upstream": "upstream", "internal": "internal"}

_END = object()

SSEMessage = dict[str, str]


def sse(event: str, data: dict[str, Any]) -> SSEMessage:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


def pitch_text(events: list[PitchEvent]) -> str | None:
    text = "".join(str(e["data"].get("text", "")) for e in events if e["event"] == "token")
    return text.strip() or None


def _safe_record(recorder: PitchRecorder | None, result: PitchRunResult) -> None:
    """Hand the result to the recorder; never let history problems reach the stream."""
    if recorder is None:
        return
    try:
        recorder.record(result)
    except Exception:  # noqa: BLE001 -- best effort, logged
        logger.exception("Pitch history recorder failed")


class PitchCache:
    """Successful runs, replayed for identical (company, role) requests. Per process."""

    def __init__(self, ttl_hours: float, maxsize: int = 512):
        self.enabled = ttl_hours > 0
        self._cache: TTLCache[tuple[str, str], list[PitchEvent]] = TTLCache(
            maxsize=maxsize, ttl=max(ttl_hours, 0.001) * 3600
        )

    @staticmethod
    def key(company: str, role: str) -> tuple[str, str]:
        return company.casefold(), role.casefold()

    def get(self, company: str, role: str) -> list[PitchEvent] | None:
        return self._cache.get(self.key(company, role)) if self.enabled else None

    def put(self, company: str, role: str, events: list[PitchEvent]) -> None:
        if self.enabled:
            self._cache[self.key(company, role)] = events


async def replay(
    events: list[PitchEvent],
    company: str = "",
    role: str = "",
    recorder: PitchRecorder | None = None,
) -> AsyncIterator[SSEMessage]:
    started = time.monotonic()
    sent: list[PitchEvent] = []
    finished = False
    try:
        for event in events:
            if event["event"] == "token":
                await asyncio.sleep(REPLAY_TOKEN_DELAY_SECONDS)
            sent.append(event)
            yield sse(event["event"], event["data"])
        finished = True
        yield sse("done", {"cached": True, "duration_ms": int((time.monotonic() - started) * 1000)})
    finally:
        # A cached replay costs nothing: zero tokens, searches and spend.
        _safe_record(
            recorder,
            PitchRunResult(
                company=company,
                role=role,
                outcome=PitchOutcome.CACHED if finished else PitchOutcome.DISCONNECTED,
                pitch_text=pitch_text(sent),
                error_code=None,
                duration_ms=int((time.monotonic() - started) * 1000),
                input_tokens=0,
                output_tokens=0,
                searches=0,
                estimated_cost_usd=Decimal(0),
            ),
        )


class PitchRun:
    def __init__(
        self,
        pipeline: PitchPipeline,
        company: str,
        role: str,
        cache: PitchCache,
        timeout_seconds: float,
        recorder: PitchRecorder | None = None,
    ):
        self.pipeline = pipeline
        self.company = company
        self.role = role
        self.cache = cache
        self.timeout_seconds = timeout_seconds
        self.recorder = recorder
        self.task: asyncio.Task[None] | None = None

    async def _produce(self, queue: asyncio.Queue[Any]) -> None:
        try:
            async for event in self.pipeline.events(self.company, self.role):
                await queue.put(event)
            await queue.put(_END)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- handed to the consumer, which reports it
            await queue.put(exc)

    async def stream(self) -> AsyncIterator[SSEMessage]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self.timeout_seconds
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self.task = asyncio.create_task(self._produce(queue))
        recorded: list[PitchEvent] = []
        outcome = "disconnected"
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
                if item is _END:
                    break
                if isinstance(item, BaseException):
                    raise item
                recorded.append(item)
                yield sse(item["event"], item["data"])

            self.cache.put(self.company, self.role, recorded)
            outcome = "ok"
            yield sse("done", {"cached": False, "duration_ms": int((loop.time() - started) * 1000)})
        except TimeoutError:
            outcome = "timeout"
            yield sse("error", {"code": "timeout", "message": MESSAGES["timeout"]})
        except PitchUpstreamError as exc:
            outcome = "upstream"
            logger.warning("Pitch Me upstream failure: {}", exc)
            yield sse("error", {"code": "upstream", "message": MESSAGES["upstream"]})
        except Exception:  # noqa: BLE001 -- logged; the visitor gets a safe error event
            outcome = "internal"
            logger.exception("Pitch Me failed unexpectedly")
            yield sse("error", {"code": "internal", "message": MESSAGES["internal"]})
        finally:
            if not self.task.done():
                self.task.cancel()  # visitor left or we timed out: stop spending money
            seconds = loop.time() - started
            self._log(outcome, seconds)
            self._record(outcome, recorded, seconds)

    def _spend(self) -> tuple[int, int, int, float]:
        """Input tokens, output tokens, searches and the estimated cost in USD for this run."""
        usage = self.pipeline.llm.usage
        searches = self.pipeline.research.searches
        cost = (
            usage.input_tokens * EST_USD_PER_1M_INPUT_TOKENS / 1_000_000
            + usage.output_tokens * EST_USD_PER_1M_OUTPUT_TOKENS / 1_000_000
            + searches * EST_USD_PER_SEARCH
        )
        return usage.input_tokens, usage.output_tokens, searches, cost

    def _record(self, outcome: str, events: list[PitchEvent], seconds: float) -> None:
        try:
            input_tokens, output_tokens, searches, cost = self._spend()
            result = PitchRunResult(
                company=self.company,
                role=self.role,
                outcome=OUTCOMES[outcome],
                pitch_text=pitch_text(events),
                error_code=ERROR_CODES.get(outcome),
                duration_ms=int(seconds * 1000),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                searches=searches,
                estimated_cost_usd=Decimal(str(round(cost, 6))),
            )
        except Exception:  # noqa: BLE001 -- best effort, logged
            logger.exception("Could not build the Pitch history entry")
            return
        _safe_record(self.recorder, result)

    def _log(self, outcome: str, seconds: float) -> None:
        input_tokens, output_tokens, searches, cost = self._spend()
        logger.info(
            "Pitch Me run outcome={} cached=false duration={:.2f}s searches={} "
            "input_tokens={} output_tokens={} est_cost_usd={:.4f}",
            outcome,
            seconds,
            searches,
            input_tokens,
            output_tokens,
            cost,
        )
