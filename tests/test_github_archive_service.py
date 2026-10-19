"""Tests for GitHubArchiveService. All GitHub HTTP calls are mocked - no live network."""

from __future__ import annotations

import asyncio
import io
import tempfile
import zipfile
from pathlib import Path

import anyio
import httpx
import pytest

import apps.api.services.github_archive as archive_module
from apps.api.config import Settings
from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubService,
    GitHubTimeoutError,
    GitHubUpstreamError,
    InvalidRepositoryURLError,
    RepositoryNotFoundError,
)
from apps.api.services.github_archive import ArchiveTooLargeError, GitHubArchiveService, MalformedArchiveError

pytestmark = pytest.mark.anyio

REACT_METADATA_PAYLOAD = {
    "id": 10270250,
    "full_name": "facebook/react",
    "default_branch": "main",
    "description": "The library for web and native user interfaces.",
}
COMMIT_SHA = "b" * 40


def _make_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("react-main/README.md", "hello")
    return buffer.getvalue()


def _service_for(handler, *, settings: Settings | None = None) -> GitHubArchiveService:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.github.com", follow_redirects=True)
    github_service = GitHubService(client=client, settings=settings or Settings())
    return GitHubArchiveService(client=client, github_service=github_service, settings=settings or Settings())


def _metadata_and_commit_handler(zipball_handler):
    """Wraps a zipball-specific handler with the standard metadata + commit-SHA responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/facebook/react":
            return httpx.Response(200, json=REACT_METADATA_PAYLOAD)
        if request.url.path == "/repos/facebook/react/commits/main":
            return httpx.Response(200, json={"sha": COMMIT_SHA})
        return zipball_handler(request)

    return handler


async def test_fetch_repository_archive_happy_path() -> None:
    zip_bytes = _make_zip_bytes()

    def zipball_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/repos/facebook/react/zipball/{COMMIT_SHA}"
        return httpx.Response(200, content=zip_bytes)

    async with _service_for(_metadata_and_commit_handler(zipball_handler)) as service:
        result = await service.fetch_repository_archive("facebook", "react")

    assert result.commit_sha == COMMIT_SHA
    assert result.archive_path.exists()
    assert zipfile.is_zipfile(result.archive_path)

    result.cleanup()
    assert not result.archive_path.exists()
    result.cleanup()  # safe to call twice


async def test_fetch_repository_archive_raises_too_large_when_streamed_bytes_exceed_cap() -> None:
    oversized_body = b"x" * 1000

    def zipball_handler(request: httpx.Request) -> httpx.Response:
        # No Content-Length claim here that would reveal the true size up front;
        # the cap must be enforced from actually-streamed bytes.
        return httpx.Response(200, content=oversized_body)

    settings = Settings(github_archive_max_bytes=100)
    async with _service_for(_metadata_and_commit_handler(zipball_handler), settings=settings) as service:
        with pytest.raises(ArchiveTooLargeError):
            await service.fetch_repository_archive("facebook", "react")


async def test_fetch_repository_archive_rejects_early_on_oversized_content_length() -> None:
    called = {"zipball": False}

    def zipball_handler(request: httpx.Request) -> httpx.Response:
        called["zipball"] = True
        return httpx.Response(200, headers={"Content-Length": "1000"}, content=b"x" * 1000)

    settings = Settings(github_archive_max_bytes=100)
    async with _service_for(_metadata_and_commit_handler(zipball_handler), settings=settings) as service:
        with pytest.raises(ArchiveTooLargeError):
            await service.fetch_repository_archive("facebook", "react")


async def test_fetch_repository_archive_strips_authorization_on_cross_host_redirect() -> None:
    seen_redirected_headers: dict[str, str] = {}
    zip_bytes = _make_zip_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/facebook/react":
            return httpx.Response(200, json=REACT_METADATA_PAYLOAD)
        if request.url.path == "/repos/facebook/react/commits/main":
            return httpx.Response(200, json={"sha": COMMIT_SHA})
        if request.url.host == "api.github.com":
            return httpx.Response(
                302,
                headers={"Location": f"https://codeload.example.test/facebook/react/zip/{COMMIT_SHA}"},
            )
        assert request.url.host == "codeload.example.test"
        seen_redirected_headers.update(request.headers)
        return httpx.Response(200, content=zip_bytes)

    settings = Settings(github_token="secret-token")
    async with _service_for(handler, settings=settings) as service:
        result = await service.fetch_repository_archive("facebook", "react")

    assert "authorization" not in seen_redirected_headers
    result.cleanup()


@pytest.mark.parametrize(
    ("status_code", "expected_exception"),
    [(404, RepositoryNotFoundError), (403, GitHubRateLimitedError), (429, GitHubRateLimitedError), (500, GitHubUpstreamError)],
)
async def test_fetch_repository_archive_maps_zipball_error_status(status_code: int, expected_exception: type) -> None:
    def zipball_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "error"})

    async with _service_for(_metadata_and_commit_handler(zipball_handler)) as service:
        with pytest.raises(expected_exception):
            await service.fetch_repository_archive("facebook", "react")


async def test_fetch_repository_archive_raises_timeout_on_zipball_timeout() -> None:
    def zipball_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    async with _service_for(_metadata_and_commit_handler(zipball_handler)) as service:
        with pytest.raises(GitHubTimeoutError):
            await service.fetch_repository_archive("facebook", "react")


async def test_fetch_repository_archive_raises_not_found_when_repo_metadata_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _service_for(handler) as service:
        with pytest.raises(RepositoryNotFoundError):
            await service.fetch_repository_archive("facebook", "does-not-exist")


async def test_fetch_repository_archive_raises_malformed_for_non_zip_content() -> None:
    def zipball_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not a zip file at all")

    async with _service_for(_metadata_and_commit_handler(zipball_handler)) as service:
        with pytest.raises(MalformedArchiveError):
            await service.fetch_repository_archive("facebook", "react")


async def test_fetch_repository_archive_rejects_invalid_owner_repo_without_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request should be made for an invalid owner/repo")

    async with _service_for(handler) as service:
        with pytest.raises(InvalidRepositoryURLError):
            await service.fetch_repository_archive("face;book", "react")


class _SlowZipStream(httpx.AsyncByteStream):
    """An async byte stream that pauses between chunks, giving a real cancellation window."""

    def __init__(self, chunks: list[bytes], delay: float) -> None:
        self._chunks = chunks
        self._delay = delay

    async def __aiter__(self):
        for chunk in self._chunks:
            await anyio.sleep(self._delay)
            yield chunk


async def test_fetch_repository_archive_cleans_up_on_cancellation() -> None:
    zip_bytes = _make_zip_bytes()
    chunks = [zip_bytes[i : i + 4] for i in range(0, len(zip_bytes), 4)] or [b""]

    def zipball_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_SlowZipStream(chunks, delay=0.05))

    created_paths: list[str] = []
    original_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        fd, path = original_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    archive_module.tempfile.mkstemp = tracking_mkstemp
    try:
        async with _service_for(_metadata_and_commit_handler(zipball_handler)) as service:
            task = asyncio.ensure_future(service.fetch_repository_archive("facebook", "react"))
            await anyio.sleep(0.08)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    finally:
        archive_module.tempfile.mkstemp = original_mkstemp

    assert created_paths, "expected a temp file to have been created"
    for raw_path in created_paths:
        assert not Path(raw_path).exists()
