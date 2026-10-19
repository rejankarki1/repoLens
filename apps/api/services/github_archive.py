"""Downloads a GitHub repository's source as a ZIP archive, pinned to a specific commit.

Only retrieves and structurally validates the archive - it never extracts or executes
anything inside it. Extraction/indexing is a later ingestion step's responsibility.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import httpx

from apps.api.config import Settings, get_settings
from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubService,
    GitHubServiceError,
    GitHubTimeoutError,
    GitHubUpstreamError,
    InvalidRepositoryURLError,
    RepositoryNotFoundError,
    _build_headers,
)
from apps.api.services.github import _NAME_PATTERN as _OWNER_REPO_PATTERN

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class ArchiveTooLargeError(GitHubServiceError):
    """The archive exceeded the configured maximum size while streaming."""


class MalformedArchiveError(GitHubServiceError):
    """The downloaded file is not a well-formed ZIP archive."""


@dataclass(frozen=True)
class ArchiveDownloadResult:
    """A downloaded archive, pinned to the commit it was fetched at.

    The caller owns `archive_path` once this is returned and is responsible for
    calling `cleanup()` when done with it (e.g. after the next ingestion step has
    read it). The service itself only guarantees cleanup on its own internal failures.
    """

    archive_path: Path
    commit_sha: str

    def cleanup(self) -> None:
        """Delete the temporary archive file. Safe to call more than once."""
        self.archive_path.unlink(missing_ok=True)


class GitHubArchiveService:
    """Resolves a repository's default branch to a commit SHA and downloads its ZIP archive."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        github_service: GitHubService | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or get_settings()
        self._settings = settings
        self._owns_client = client is None
        self._owns_github_service = github_service is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.github_api_base_url,
            timeout=httpx.Timeout(
                connect=10.0,
                read=settings.github_archive_read_timeout_seconds,
                write=10.0,
                pool=10.0,
            ),
            headers=_build_headers(settings.github_token),
            follow_redirects=True,
        )
        self._github_service = github_service or GitHubService(settings=settings)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        if self._owns_github_service:
            await self._github_service.aclose()

    async def __aenter__(self) -> GitHubArchiveService:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def fetch_repository_archive(self, owner: str, repo: str) -> ArchiveDownloadResult:
        """Resolve `owner/repo`'s default branch to a commit and download its ZIP archive."""
        if not _OWNER_REPO_PATTERN.fullmatch(owner) or not _OWNER_REPO_PATTERN.fullmatch(repo):
            raise InvalidRepositoryURLError(f"Invalid repository owner/name: {owner!r}/{repo!r}")

        metadata = await self._github_service.get_repository_metadata(f"https://github.com/{owner}/{repo}")
        sha = await self._github_service.get_commit_sha(owner, repo, metadata.default_branch)

        if not _SHA_PATTERN.fullmatch(sha):
            raise GitHubUpstreamError(f"GitHub returned an unexpected commit SHA for {owner}/{repo}")

        archive_path = await self._download_zipball(owner, repo, sha)
        return ArchiveDownloadResult(archive_path=archive_path, commit_sha=sha)

    async def _download_zipball(self, owner: str, repo: str, sha: str) -> Path:
        context = f"{owner}/{repo}@{sha}"
        max_bytes = self._settings.github_archive_max_bytes

        try:
            async with self._client.stream("GET", f"/repos/{owner}/{repo}/zipball/{sha}") as response:
                if response.status_code == 404:
                    raise RepositoryNotFoundError(f"Archive not found for {context}")
                if response.status_code in (403, 429):
                    raise GitHubRateLimitedError(f"GitHub rate limit exceeded while downloading {context}")
                if response.is_error:
                    raise GitHubUpstreamError(
                        f"GitHub returned an unexpected error ({response.status_code}) downloading {context}"
                    )

                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise ArchiveTooLargeError(
                        f"Archive for {context} reports {content_length} bytes, exceeding the {max_bytes} byte limit"
                    )

                path = await self._stream_to_tempfile(response, max_bytes=max_bytes, context=context)
        except httpx.TimeoutException as exc:
            raise GitHubTimeoutError(f"Timed out downloading archive for {context}") from exc
        except httpx.HTTPError as exc:
            raise GitHubUpstreamError(f"Request to GitHub failed while downloading {context}: {exc}") from exc

        if not zipfile.is_zipfile(path):
            path.unlink(missing_ok=True)
            raise MalformedArchiveError(f"Downloaded archive for {context} is not a valid ZIP file")

        return path

    async def _stream_to_tempfile(self, response: httpx.Response, *, max_bytes: int, context: str) -> Path:
        fd, raw_path = tempfile.mkstemp(suffix=".zip", prefix="repolens-archive-")
        path = Path(raw_path)
        total = 0
        try:
            with open(fd, "wb") as file:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ArchiveTooLargeError(
                            f"Archive for {context} exceeded the {max_bytes} byte limit while streaming"
                        )
                    file.write(chunk)
        except BaseException:
            # Covers ArchiveTooLargeError, httpx errors, and cancellation (CancelledError
            # is a BaseException, not an Exception) - never leave a partial file behind.
            path.unlink(missing_ok=True)
            raise
        return path


async def get_archive_service() -> AsyncIterator[GitHubArchiveService]:
    """FastAPI dependency: a request-scoped `GitHubArchiveService`, closed after the request."""
    async with GitHubArchiveService() as service:
        yield service
