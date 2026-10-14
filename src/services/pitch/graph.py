"""The three-agent pipeline: researcher -> matchmaker -> writer (LangGraph).

Nodes report progress with LangGraph's custom stream writer, so `PitchPipeline.events()`
yields agent / log / token events in the order they happen.
"""

from collections.abc import AsyncIterator
from typing import Any, Literal, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from src.services.pitch.llm import PitchLLM, PitchUpstreamError
from src.services.pitch.prompts import (
    format_research,
    matchmaker_messages,
    search_query,
    writer_messages,
)
from src.services.pitch.research import ResearchClient

AgentName = Literal["researcher", "matchmaker", "writer"]


class PitchEvent(TypedDict):
    event: str
    data: dict[str, Any]


class PitchState(TypedDict, total=False):
    company: str
    role: str
    research: str
    matches: str
    pitch: str


def _emit(event: str, **data: Any) -> None:
    get_stream_writer()({"event": event, "data": data})


def _agent(agent: AgentName, status: str) -> None:
    _emit("agent", agent=agent, status=status)


def _log(agent: AgentName, message: str) -> None:
    _emit("log", agent=agent, message=message)


class PitchPipeline:
    def __init__(self, llm: PitchLLM, research: ResearchClient):
        self.llm = llm
        self.research = research
        self._graph = self._build()

    def _build(self):
        graph = StateGraph(PitchState)
        graph.add_node("researcher", self._researcher)
        graph.add_node("matchmaker", self._matchmaker)
        graph.add_node("writer", self._writer)
        graph.add_edge(START, "researcher")
        graph.add_edge("researcher", "matchmaker")
        graph.add_edge("matchmaker", "writer")
        graph.add_edge("writer", END)
        return graph.compile()

    async def _researcher(self, state: PitchState) -> PitchState:
        _agent("researcher", "running")
        _log("researcher", f"Searching the web for {state['company']}")
        results = await self.research.search(search_query(state["company"], state["role"]))
        _log("researcher", f"Read {len(results)} source{'s' if len(results) != 1 else ''}")
        _agent("researcher", "done")
        return {"research": format_research(results)}

    async def _matchmaker(self, state: PitchState) -> PitchState:
        _agent("matchmaker", "running")
        _log("matchmaker", f"Matching {state['company']}'s needs to my projects")
        system, user = matchmaker_messages(state["company"], state["role"], state["research"])
        matches = await self.llm.complete(system, user)
        _log("matchmaker", "Picked the most relevant experience")
        _agent("matchmaker", "done")
        return {"matches": matches}

    async def _writer(self, state: PitchState) -> PitchState:
        _agent("writer", "running")
        _log("writer", "Writing the pitch")
        system, user = writer_messages(
            state["company"], state["role"], state["research"], state["matches"]
        )
        parts: list[str] = []
        async for chunk in self.llm.stream(system, user):
            parts.append(chunk)
            _emit("token", text=chunk)
        pitch = "".join(parts).strip()
        if not pitch:
            raise PitchUpstreamError("The writer returned no text")
        _agent("writer", "done")
        return {"pitch": pitch}

    async def events(self, company: str, role: str) -> AsyncIterator[PitchEvent]:
        async for payload in self._graph.astream(
            {"company": company, "role": role}, stream_mode="custom"
        ):
            yield payload
