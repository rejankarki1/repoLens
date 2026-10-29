"""Answer generation with Google Gemini (Flash): one small client, streaming and non-streaming.

This module also holds what both providers share (`GenerationError`, result types, the `Generator` protocol)
and `get_generator()`, which picks Gemini or Groq (`groq_generation.py`) from GENERATION_PROVIDER.

Uses the native `generateContent` / `streamGenerateContent?alt=sse` endpoints with the same
`GEMINI_API_KEY` as embeddings, sent in the `x-goog-api-key` header (never in a URL, which libraries log).
No framework. Every failure is a `GenerationError` with a stable `code`, so the API can
report it clearly; an answer that stops for any reason other than finishing normally (cut off by the token
limit, blocked, empty, stream dropped) is an error, never a partial success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import httpx

from apps.api.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Gemini finish reasons that mean "stopped normally"; everything else is not a complete answer.
_COMPLETE = {"STOP"}


class GenerationError(Exception):
    """A failed or incomplete generation.

    `code`: not_configured | rate_limited | unavailable | timeout | auth | bad_request | blocked | truncated |
    empty | interrupted. `retry_after` is seconds the provider asked us to wait (rate limits).
    """

    def __init__(self, code: str, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class Finished:
    finish_reason: str
    prompt_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    finish_reason: str = "STOP"
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Generator(Protocol):
    """What `rag.py` needs from an answer provider (`GeminiGenerator`, `GroqGenerator`)."""

    provider: str
    model: str

    async def generate(self, system: str, contents: list[dict[str, Any]], *, attempts: int = 2) -> GenerationResult: ...

    def stream(self, system: str, contents: list[dict[str, Any]]) -> AsyncIterator[TextDelta | Finished]: ...


def _error_for(status: int, body: str, headers: httpx.Headers) -> GenerationError:
    """Map an HTTP error to a GenerationError. The provider's message is never echoed (it may repeat input)."""
    if status == 429:
        delay: float | None = None
        try:
            header = headers.get("retry-after")
            delay = float(header) if header else None
            for detail in json.loads(body).get("error", {}).get("details", []):
                if str(detail.get("@type", "")).endswith("RetryInfo"):
                    delay = float(str(detail.get("retryDelay", "")).rstrip("s"))
        except (ValueError, TypeError, AttributeError):
            pass
        return GenerationError("rate_limited", "Gemini is rate limiting requests; try again shortly", status_code=429, retry_after=delay)
    if status in (401, 403) or (status == 400 and "API_KEY_INVALID" in body):
        return GenerationError("auth", "Gemini rejected the API key", status_code=status)
    if status >= 500:
        return GenerationError("unavailable", f"Gemini is unavailable ({status})", status_code=status)
    return GenerationError("bad_request", f"Gemini rejected the request ({status})", status_code=status)


def _candidate(data: dict[str, Any]) -> tuple[str, str | None]:
    """(text, finish reason) of the first candidate in a response or stream chunk."""
    error = data.get("error")
    if isinstance(error, dict):  # Gemini reports overload mid-stream as a `data: {"error": ...}` chunk
        code = error.get("code")
        raise _error_for(code if isinstance(code, int) else 503, json.dumps(data), httpx.Headers())
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GenerationError("blocked", f"Gemini blocked the request ({block})")
    candidates = data.get("candidates") or []
    if not candidates:
        return "", None
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))  # thinking parts are not answer text
    return text, candidate.get("finishReason")


def _check_finish(reason: str | None, text: str) -> None:
    if reason is None:
        raise GenerationError("interrupted", "The answer stream ended before Gemini finished")
    if reason == "MAX_TOKENS":
        raise GenerationError("truncated", "The answer was cut off by the output token limit")
    if reason not in _COMPLETE:
        raise GenerationError("blocked", f"Gemini stopped the answer ({reason})")
    if not text.strip():
        raise GenerationError("empty", "Gemini returned an empty answer")


class GeminiGenerator:
    provider = "gemini"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        settings = settings or get_settings()
        if not settings.embeddings_enabled:  # the key is shared with embeddings
            raise GenerationError("not_configured", "Answer generation is not configured (set GEMINI_API_KEY)")
        assert settings.gemini_api_key is not None
        self.model = settings.gemini_generation_model
        base = f"{settings.gemini_api_base_url.rstrip('/')}/models/{self.model}"
        self._generate_url = f"{base}:generateContent"
        self._stream_url = f"{base}:streamGenerateContent?alt=sse"
        self._headers = {"x-goog-api-key": settings.gemini_api_key.get_secret_value()}
        self._timeout = httpx.Timeout(connect=10.0, read=settings.gemini_generation_timeout_seconds, write=10.0, pool=10.0)
        self._deadline = settings.gemini_generation_deadline_seconds
        self._temperature = settings.gemini_generation_temperature
        self._max_tokens = settings.gemini_generation_max_output_tokens
        self._thinking_budget = settings.gemini_thinking_budget
        self._client = client
        self._sleep = sleep

    def _body(self, system: str, contents: list[dict[str, Any]]) -> dict[str, Any]:
        config: dict[str, Any] = {"temperature": self._temperature, "maxOutputTokens": self._max_tokens}
        if self._thinking_budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}
        return {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": config}

    async def generate(self, system: str, contents: list[dict[str, Any]], *, attempts: int = 2) -> GenerationResult:
        """One complete answer. Server errors and network failures are retried once; a rate limit is not
        (the caller reports it with its retry delay)."""
        body = self._body(system, contents)
        for attempt in range(1, attempts + 1):
            try:
                return await self._generate_once(body)
            except GenerationError as exc:
                if exc.code not in ("unavailable", "timeout") or attempt == attempts:
                    raise
                await self._sleep(1.0)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _generate_once(self, body: dict[str, Any]) -> GenerationResult:
        try:
            if self._client is not None:
                response = await self._client.post(self._generate_url, json=body, headers=self._headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(self._generate_url, json=body, headers=self._headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise GenerationError("timeout", "Gemini did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise GenerationError("unavailable", f"Could not reach Gemini ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise _error_for(response.status_code, response.text, response.headers)
        try:
            data = response.json()
            text, reason = _candidate(data)
        except ValueError as exc:
            raise GenerationError("bad_request", "Gemini sent an unreadable response") from exc
        _check_finish(reason, text)
        usage = data.get("usageMetadata") or {}
        return GenerationResult(text, reason or "STOP", usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))

    async def stream(self, system: str, contents: list[dict[str, Any]]) -> AsyncIterator[TextDelta | Finished]:
        """Yield text as it arrives, then `Finished`. No retries: partial output may already have been shown.

        Raises `GenerationError` for anything but a normal finish. Closing the iterator early (a client that
        disconnected) closes the HTTP stream.
        """
        body = self._body(system, contents)
        started = time.monotonic()
        text_so_far, reason, usage = "", None, {}
        client = self._client or httpx.AsyncClient()
        try:
            async with client.stream("POST", self._stream_url, json=body, headers=self._headers, timeout=self._timeout) as response:
                if response.status_code >= 400:
                    raise _error_for(response.status_code, (await response.aread()).decode("utf-8", "replace"), response.headers)
                async for line in response.aiter_lines():
                    if time.monotonic() - started > self._deadline:
                        raise GenerationError("timeout", "The answer took too long")
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                        chunk, chunk_reason = _candidate(data)
                    except ValueError as exc:
                        raise GenerationError("interrupted", "Gemini sent an unreadable stream chunk") from exc
                    usage = data.get("usageMetadata") or usage
                    if chunk:
                        text_so_far += chunk
                        yield TextDelta(chunk)
                    if chunk_reason:
                        reason = chunk_reason
        except httpx.TimeoutException as exc:
            raise GenerationError("timeout", "Gemini stopped responding") from exc
        except httpx.HTTPError as exc:
            raise GenerationError("interrupted", f"The connection to Gemini was lost ({type(exc).__name__})") from exc
        finally:
            if self._client is None:
                await client.aclose()
        _check_finish(reason, text_so_far)
        yield Finished(reason or "STOP", usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))


@lru_cache
def _shared_generator() -> Generator | None:
    """The provider chosen by GENERATION_PROVIDER, or None when its key is missing. No fallback to the other one."""
    try:
        if get_settings().generation_provider == "groq":
            from apps.api.services.groq_generation import GroqGenerator

            return GroqGenerator()
        return GeminiGenerator()
    except GenerationError:
        return None


def get_generator() -> Generator | None:
    """FastAPI dependency: the process-wide generator, or None when the selected provider has no key."""
    return _shared_generator()
