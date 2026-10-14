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
from typing import Any

from cachetools import TTLCache
from loguru import logger

from src.services.pitch.graph import PitchEvent, PitchPipeline
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

_END = object()

SSEMessage = dict[str, str]


def sse(event: str, data: dict[str, Any]) -> SSEMessage:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


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


async def replay(events: list[PitchEvent]) -> AsyncIterator[SSEMessage]:
    started = time.monotonic()
    for event in events:
        if event["event"] == "token":
            await asyncio.sleep(REPLAY_TOKEN_DELAY_SECONDS)
        yield sse(event["event"], event["data"])
    yield sse("done", {"cached": True, "duration_ms": int((time.monotonic() - started) * 1000)})


class PitchRun:
    def __init__(
        self,
        pipeline: PitchPipeline,
        company: str,
        role: str,
        cache: PitchCache,
        timeout_seconds: float,
    ):
        self.pipeline = pipeline
        self.company = company
        self.role = role
        self.cache = cache
        self.timeout_seconds = timeout_seconds
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
            self._log(outcome, loop.time() - started)

    def _log(self, outcome: str, seconds: float) -> None:
        usage = self.pipeline.llm.usage
        searches = self.pipeline.research.searches
        cost = (
            usage.input_tokens * EST_USD_PER_1M_INPUT_TOKENS / 1_000_000
            + usage.output_tokens * EST_USD_PER_1M_OUTPUT_TOKENS / 1_000_000
            + searches * EST_USD_PER_SEARCH
        )
        logger.info(
            "Pitch Me run outcome={} cached=false duration={:.2f}s searches={} "
            "input_tokens={} output_tokens={} est_cost_usd={:.4f}",
            outcome,
            seconds,
            searches,
            usage.input_tokens,
            usage.output_tokens,
            cost,
        )
