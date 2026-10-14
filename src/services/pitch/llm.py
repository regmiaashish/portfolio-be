"""The language model behind Pitch Me, chosen in one place (`create_llm`) so it can be swapped."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from src.core.config import settings

DEV_FAKE_TOKEN_DELAY_SECONDS = 0.04


class PitchUpstreamError(Exception):
    """An AI or search provider failed. The message is internal; visitors see a generic one."""


@dataclass
class Usage:
    """Token counts reported by the provider for one run (zero when it reports nothing)."""

    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage_metadata: dict[str, Any] | None) -> None:
        if usage_metadata:
            self.input_tokens += int(usage_metadata.get("input_tokens") or 0)
            self.output_tokens += int(usage_metadata.get("output_tokens") or 0)


class PitchLLM(Protocol):
    usage: Usage

    async def complete(self, system: str, user: str) -> str: ...

    def stream(self, system: str, user: str) -> AsyncIterator[str]: ...


class GeminiLLM:
    """Google Gemini through langchain-google-genai."""

    def __init__(self, api_key: str, model: str):
        from langchain_google_genai import ChatGoogleGenerativeAI

        self.usage = Usage()
        self._chat = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=0.4,
            max_output_tokens=400,
            max_retries=1,
            timeout=30,
        )

    @staticmethod
    def _messages(system: str, user: str) -> list[tuple[str, str]]:
        return [("system", system), ("human", user)]

    async def complete(self, system: str, user: str) -> str:
        try:
            message = await self._chat.ainvoke(self._messages(system, user))
        except Exception as exc:
            raise PitchUpstreamError(f"Gemini completion failed: {type(exc).__name__}") from exc
        self.usage.add(getattr(message, "usage_metadata", None))
        return str(message.content).strip()

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        try:
            async for chunk in self._chat.astream(self._messages(system, user)):
                self.usage.add(getattr(chunk, "usage_metadata", None))
                if chunk.content:
                    yield str(chunk.content)
        except Exception as exc:
            raise PitchUpstreamError(f"Gemini stream failed: {type(exc).__name__}") from exc


class FakeLLM:
    """Deterministic, free stand-in for tests and local development."""

    PITCH = (
        "Hi {company}, I'm Aashish, a backend engineer who builds FastAPI services and LLM "
        "integrations that run in production. For a {role} role I'd bring the API and AI pipeline "
        "work behind Fagoon Upgrade and Alliance. I'd love to talk about what your team is building."
    )

    def __init__(self, token_delay: float = 0.0) -> None:
        self.usage = Usage()
        self.calls: list[str] = []
        # Local dev uses a small delay so the UI streams like the real model; tests use 0.
        self.token_delay = token_delay

    @staticmethod
    def _tag(text: str, tag: str) -> str:
        start, end = f"<{tag}>", f"</{tag}>"
        return text.split(start, 1)[1].split(end, 1)[0] if start in text else ""

    async def complete(self, system: str, user: str) -> str:
        self.calls.append("complete")
        return (
            "- Fagoon Upgrade: LLM workflows in production\n- Alliance: Twilio + voice AI pipeline"
        )

    async def stream(self, system: str, user: str) -> AsyncIterator[str]:
        self.calls.append("stream")
        text = self.PITCH.format(company=self._tag(user, "company"), role=self._tag(user, "role"))
        for word in text.split(" "):
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            yield word + " "


def create_llm() -> PitchLLM:
    """The only place that knows which provider is configured. Add new providers here."""
    if settings.pitch_llm_provider == "fake":
        return FakeLLM(token_delay=DEV_FAKE_TOKEN_DELAY_SECONDS)
    return GeminiLLM(settings.gemini_api_key.get_secret_value(), settings.pitch_llm_model)
