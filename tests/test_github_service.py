"""Tests for the GitHub repository metadata service. No live network requests."""

import httpx
import pytest

from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubService,
    GitHubTimeoutError,
    GitHubUpstreamError,
    InvalidRepositoryURLError,
    RepositoryNotFoundError,
    _build_headers,
    parse_github_url,
)

pytestmark = pytest.mark.anyio

REACT_PAYLOAD = {
    "id": 10270250,
    "full_name": "facebook/react",
    "default_branch": "main",
    "description": "The library for web and native user interfaces.",
}


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/facebook/react",
        "http://github.com/facebook/react/",
        "https://www.github.com/facebook/react.git",
    ],
)
def test_parse_github_url_accepts_valid_urls(url: str) -> None:
    assert parse_github_url(url) == ("facebook", "react")


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.com/facebook/react",
        "https://github.com/facebook",
        "https://github.com/facebook/re:act",
    ],
)
def test_parse_github_url_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(InvalidRepositoryURLError):
        parse_github_url(url)


def _service_for(handler, *, follow_redirects: bool = False) -> GitHubService:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport, base_url="https://api.github.com", follow_redirects=follow_redirects
    )
    return GitHubService(client=client)


async def test_get_repository_metadata_returns_metadata_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/facebook/react"
        return httpx.Response(200, json=REACT_PAYLOAD)

    async with _service_for(handler) as service:
        metadata = await service.get_repository_metadata("https://github.com/facebook/react")

    assert metadata.github_repo_id == 10270250
    assert metadata.full_name == "facebook/react"
    assert metadata.default_branch == "main"
    assert metadata.description == "The library for web and native user interfaces."


async def test_get_repository_metadata_follows_renamed_repository_redirect() -> None:
    """GitHub 301s a lookup by old name/owner to the canonical repo - the real client must follow it."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/old-owner/old-name":
            return httpx.Response(
                301,
                headers={"Location": "https://api.github.com/repositories/10270250"},
                json={"message": "Moved Permanently", "url": "https://api.github.com/repositories/10270250"},
            )
        assert request.url.path == "/repositories/10270250"
        return httpx.Response(200, json=REACT_PAYLOAD)

    async with _service_for(handler, follow_redirects=True) as service:
        metadata = await service.get_repository_metadata("https://github.com/old-owner/old-name")

    assert metadata.github_repo_id == 10270250
    assert metadata.full_name == "facebook/react"


async def test_get_repository_metadata_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _service_for(handler) as service:
        with pytest.raises(RepositoryNotFoundError):
            await service.get_repository_metadata("https://github.com/facebook/does-not-exist")


async def test_get_repository_metadata_raises_rate_limited_on_403() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"Retry-After": "30"},
            json={"message": "API rate limit exceeded"},
        )

    async with _service_for(handler) as service:
        with pytest.raises(GitHubRateLimitedError) as exc_info:
            await service.get_repository_metadata("https://github.com/facebook/react")

    assert exc_info.value.retry_after == 30.0


async def test_get_repository_metadata_raises_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    async with _service_for(handler) as service:
        with pytest.raises(GitHubTimeoutError):
            await service.get_repository_metadata("https://github.com/facebook/react")


async def test_get_repository_metadata_raises_upstream_error_on_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "Internal Server Error"})

    async with _service_for(handler) as service:
        with pytest.raises(GitHubUpstreamError):
            await service.get_repository_metadata("https://github.com/facebook/react")


async def test_get_commit_sha_returns_sha_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/facebook/react/commits/main"
        return httpx.Response(200, json={"sha": "a" * 40})

    async with _service_for(handler) as service:
        sha = await service.get_commit_sha("facebook", "react", "main")

    assert sha == "a" * 40


async def test_get_commit_sha_raises_not_found_on_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with _service_for(handler) as service:
        with pytest.raises(RepositoryNotFoundError):
            await service.get_commit_sha("facebook", "does-not-exist", "main")


def _client_with_headers(token: str | None, handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.com",
        headers=_build_headers(token),
    )


async def test_authorization_header_present_only_when_token_configured() -> None:
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json=REACT_PAYLOAD)

    async with GitHubService(client=_client_with_headers("secret-token", handler)) as service:
        await service.get_repository_metadata("https://github.com/facebook/react")
    assert seen_headers.get("authorization") == "Bearer secret-token"

    seen_headers.clear()
    async with GitHubService(client=_client_with_headers(None, handler)) as service:
        await service.get_repository_metadata("https://github.com/facebook/react")
    assert "authorization" not in seen_headers
