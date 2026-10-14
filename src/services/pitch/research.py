"""Web research for the researcher agent: one search, a handful of results."""

from dataclasses import dataclass
from typing import Protocol

from src.core.config import settings
from src.services.pitch.llm import PitchUpstreamError

MAX_RESULTS = 5


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str


class ResearchClient(Protocol):
    searches: int

    async def search(self, query: str) -> list[SearchResult]: ...


class TavilyResearch:
    def __init__(self, api_key: str):
        from tavily import AsyncTavilyClient

        self.searches = 0
        self._client = AsyncTavilyClient(api_key=api_key)

    async def search(self, query: str) -> list[SearchResult]:
        self.searches += 1
        try:
            response = await self._client.search(
                query, search_depth="basic", max_results=MAX_RESULTS, timeout=15
            )
        except Exception as exc:
            raise PitchUpstreamError(f"Tavily search failed: {type(exc).__name__}") from exc
        return [
            SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                content=str(item.get("content") or ""),
            )
            for item in response.get("results", [])[:MAX_RESULTS]
        ]


class FakeResearch:
    """Canned results for tests and local development. Never touches the network."""

    def __init__(self) -> None:
        self.searches = 0
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.searches += 1
        self.queries.append(query)
        return [
            SearchResult("Engineering blog", "https://example.com/blog", "We run Python services."),
            SearchResult("Careers", "https://example.com/careers", "Hiring backend engineers."),
        ]


def create_research() -> ResearchClient:
    if settings.pitch_research_provider == "fake":
        return FakeResearch()
    return TavilyResearch(settings.tavily_api_key.get_secret_value())
