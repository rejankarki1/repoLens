"""Answer generation with GroqCloud, behind the same interface as `GeminiGenerator`.

Groq's chat API is OpenAI-compatible. `rag.py` builds Gemini-shaped `contents` (roles user/model, text parts);
this client converts them to chat messages, so prompt construction is identical for both providers. Failures
are the same `GenerationError` codes as Gemini, and an answer that does not stop normally is an error, never a
partial success. The key is sent only in the Authorization header.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from apps.api.config import Settings, get_settings
from apps.api.services.generation import Finished, GenerationError, GenerationResult, TextDelta

logger = logging.getLogger(__name__)


def _error_for(status: int, body: str, headers: httpx.Headers, model: str) -> GenerationError:
    """Map an HTTP error to a GenerationError. The provider's message is never echoed (it may repeat input)."""
    if status == 429:
        delay: float | None = None
        try:
            header = headers.get("retry-after")  # seconds; Groq sets it on 429 only
            delay = float(header) if header else None
        except ValueError:
            pass
        return GenerationError("rate_limited", "Groq is rate limiting requests; try again shortly", status_code=429, retry_after=delay)
    if status == 413:  # the request alone exceeds the model's tokens-per-minute limit
        return GenerationError(
            "bad_request",
            "The question and code context are too large for this Groq model's limit; lower RAG_CONTEXT_MAX_CHARS",
            status_code=413,
        )
    if status in (401, 403):
        return GenerationError("auth", "Groq rejected the API key", status_code=status)
    if status == 404:
        return GenerationError("bad_request", f"The Groq model {model!r} was not found or is not available to this key", status_code=404)
    if status >= 500:
        return GenerationError("unavailable", f"Groq is unavailable ({status})", status_code=status)
    return GenerationError("bad_request", f"Groq rejected the request ({status})", status_code=status)


def _chunk_error(data: dict[str, Any]) -> GenerationError:
    """An `{"error": {...}}` object delivered inside a stream or a 200 response."""
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    kind = f"{error.get('type', '')} {error.get('code', '')}".lower()
    if "rate_limit" in kind:
        return GenerationError("rate_limited", "Groq is rate limiting requests; try again shortly", status_code=429)
    return GenerationError("unavailable", "Groq reported an error while answering")


def _check_finish(reason: str | None, text: str) -> None:
    if reason is None:
        raise GenerationError("interrupted", "The answer stream ended before Groq finished")
    if reason == "length":
        raise GenerationError("truncated", "The answer was cut off by the output token limit")
    if reason != "stop":
        raise GenerationError("blocked", f"Groq stopped the answer ({reason})")
    if not text.strip():
        raise GenerationError("empty", "Groq returned an empty answer")


def _messages(system: str, contents: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system}]
    for turn in contents:
        text = "".join(part.get("text", "") for part in turn.get("parts", []))
        messages.append({"role": "assistant" if turn.get("role") == "model" else "user", "content": text})
    return messages


class GroqGenerator:
    provider = "groq"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        settings = settings or get_settings()
        if settings.groq_api_key is None or not settings.groq_api_key.get_secret_value():
            raise GenerationError("not_configured", "Answer generation is not configured (set GROQ_API_KEY)")
        self.model = settings.groq_generation_model
        self._url = f"{settings.groq_api_base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}"}
        self._timeout = httpx.Timeout(connect=10.0, read=settings.gemini_generation_timeout_seconds, write=10.0, pool=10.0)
        self._deadline = settings.gemini_generation_deadline_seconds
        self._temperature = settings.gemini_generation_temperature
        self._max_tokens = settings.gemini_generation_max_output_tokens
        self._reasoning_effort = settings.groq_reasoning_effort
        self._client = client
        self._sleep = sleep

    def _body(self, system: str, contents: list[dict[str, Any]], *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": _messages(system, contents),
            "temperature": self._temperature,
            "max_completion_tokens": self._max_tokens,
            "stream": stream,
        }
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        return body

    async def generate(self, system: str, contents: list[dict[str, Any]], *, attempts: int = 2) -> GenerationResult:
        """One complete answer. Server errors and network failures are retried once; a rate limit is not
        (the caller reports it with its retry delay)."""
        body = self._body(system, contents, stream=False)
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
                response = await self._client.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(self._url, json=body, headers=self._headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise GenerationError("timeout", "Groq stopped responding") from exc
        except httpx.HTTPError as exc:
            raise GenerationError("unavailable", f"Could not reach Groq ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise _error_for(response.status_code, response.text, response.headers, self.model)
        try:
            data = response.json()
        except ValueError as exc:
            raise GenerationError("unavailable", "Groq sent an unreadable response") from exc
        if isinstance(data, dict) and data.get("error"):
            raise _chunk_error(data)
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""  # reasoning, if any, is a separate field
        _check_finish(choice.get("finish_reason"), text)
        usage = data.get("usage") or {}
        return GenerationResult(text, "STOP", usage.get("prompt_tokens"), usage.get("completion_tokens"))

    async def stream(self, system: str, contents: list[dict[str, Any]]) -> AsyncIterator[TextDelta | Finished]:
        """Yield text as it arrives, then `Finished`. No retries: partial output may already have been shown.

        Raises `GenerationError` for anything but a normal finish. Closing the iterator early (a client that
        disconnected) closes the HTTP stream.
        """
        body = self._body(system, contents, stream=True)
        started = time.monotonic()
        text_so_far, reason = "", None
        client = self._client or httpx.AsyncClient()
        try:
            async with client.stream("POST", self._url, json=body, headers=self._headers, timeout=self._timeout) as response:
                if response.status_code >= 400:
                    raise _error_for(response.status_code, (await response.aread()).decode("utf-8", "replace"), response.headers, self.model)
                async for line in response.aiter_lines():
                    if time.monotonic() - started > self._deadline:
                        raise GenerationError("timeout", "The answer took too long")
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError as exc:
                        raise GenerationError("interrupted", "Groq sent an unreadable stream chunk") from exc
                    if isinstance(data, dict) and data.get("error"):
                        raise _chunk_error(data)
                    choice = (data.get("choices") or [{}])[0]
                    chunk = (choice.get("delta") or {}).get("content") or ""
                    if chunk:
                        text_so_far += chunk
                        yield TextDelta(chunk)
                    reason = choice.get("finish_reason") or reason
        except httpx.TimeoutException as exc:
            raise GenerationError("timeout", "Groq stopped responding") from exc
        except httpx.HTTPError as exc:
            raise GenerationError("interrupted", f"The connection to Groq was lost ({type(exc).__name__})") from exc
        finally:
            if self._client is None:
                await client.aclose()
        _check_finish(reason, text_so_far)
        yield Finished("STOP")
