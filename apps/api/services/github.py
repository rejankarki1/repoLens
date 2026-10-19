"""Async client for fetching public repository metadata from the GitHub REST API."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType
from urllib.parse import urlparse

import httpx

from apps.api.config import Settings, get_settings

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class GitHubServiceError(Exception):
    """Base error for all GitHub service failures."""


class InvalidRepositoryURLError(GitHubServiceError):
    """The given string is not a well-formed `https://github.com/{owner}/{repo}` URL."""


class RepositoryNotFoundError(GitHubServiceError):
    """GitHub returned 404 - the repository doesn't exist or isn't visible to us."""


class GitHubRateLimitedError(GitHubServiceError):
    """GitHub returned 403/429 for exceeding the API rate limit."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GitHubTimeoutError(GitHubServiceError):
    """The request to GitHub timed out."""


class GitHubUpstreamError(GitHubServiceError):
    """GitHub responded with an unexpected error or malformed payload."""


@dataclass(frozen=True)
class GitHubRepositoryMetadata:
    github_repo_id: int
    full_name: str
    default_branch: str
    description: str | None


def parse_github_url(url: str) -> tuple[str, str]:
    """Validate and normalize a GitHub repository URL to an `(owner, repo)` pair."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise InvalidRepositoryURLError(f"Could not parse URL: {url!r}") from exc

    if parsed.scheme not in ("http", "https"):
        raise InvalidRepositoryURLError(f"URL must use http or https: {url!r}")

    if parsed.hostname not in _GITHUB_HOSTS:
        raise InvalidRepositoryURLError(f"URL must be a github.com repository URL: {url!r}")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        raise InvalidRepositoryURLError(f"URL must have the form /{{owner}}/{{repo}}: {url!r}")

    owner, repo = segments
    repo = repo.removesuffix(".git")

    if not _NAME_PATTERN.fullmatch(owner) or not _NAME_PATTERN.fullmatch(repo):
        raise InvalidRepositoryURLError(f"Owner/repository contain invalid characters: {url!r}")

    return owner, repo


class GitHubService:
    """Fetches repository metadata from the GitHub REST API."""

    def __init__(self, client: httpx.AsyncClient | None = None, *, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.github_api_base_url,
            timeout=httpx.Timeout(settings.github_request_timeout_seconds),
            headers=_build_headers(settings.github_token),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> GitHubService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def get_repository_metadata(self, repo_url: str) -> GitHubRepositoryMetadata:
        """Fetch metadata for the repository at `repo_url`."""
        owner, repo = parse_github_url(repo_url)
        payload = await self._get_json(f"/repos/{owner}/{repo}", context=f"{owner}/{repo}")

        try:
            return GitHubRepositoryMetadata(
                github_repo_id=payload["id"],
                full_name=payload["full_name"],
                default_branch=payload["default_branch"],
                description=payload.get("description"),
            )
        except KeyError as exc:
            raise GitHubUpstreamError(f"GitHub returned a malformed response for {owner}/{repo}") from exc

    async def get_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        """Resolve a branch/tag/ref to the SHA of its tip commit."""
        context = f"{owner}/{repo}@{ref}"
        payload = await self._get_json(f"/repos/{owner}/{repo}/commits/{ref}", context=context)
        try:
            return payload["sha"]
        except KeyError as exc:
            raise GitHubUpstreamError(f"GitHub returned a malformed commit response for {context}") from exc

    async def _get_json(self, path: str, *, context: str) -> dict:
        """GET `path` and return its parsed JSON body, translating transport/HTTP errors."""
        try:
            response = await self._client.get(path)
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError(f"Timed out requesting {context} from GitHub") from exc
        except httpx.HTTPError as exc:
            raise GitHubUpstreamError(f"Request to GitHub failed: {exc}") from exc

        if response.status_code == 404:
            raise RepositoryNotFoundError(f"Repository not found or not accessible: {context}")

        if response.status_code in (403, 429):
            raise GitHubRateLimitedError(
                f"GitHub rate limit exceeded while fetching {context}",
                retry_after=_parse_retry_after(response.headers),
            )

        if response.is_error:
            raise GitHubUpstreamError(
                f"GitHub returned an unexpected error ({response.status_code}) for {context}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise GitHubUpstreamError(f"GitHub returned a malformed response for {context}") from exc


async def get_github_service() -> AsyncIterator[GitHubService]:
    """FastAPI dependency: a request-scoped `GitHubService`, closed after the request."""
    async with GitHubService() as service:
        yield service


def _build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "RepoLens",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_retry_after(headers: httpx.Headers) -> float | None:
    for header_name in ("Retry-After", "X-RateLimit-Reset"):
        value = headers.get(header_name)
        if value is not None:
            try:
                return float(value)
            except ValueError:
                continue
    return None
