"""Tests for POST /repositories. GitHub and the database are both mocked here."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from apps.api.models.entities import Repository
from apps.api.services.github import (
    GitHubRateLimitedError,
    GitHubRepositoryMetadata,
    GitHubTimeoutError,
    GitHubUpstreamError,
    InvalidRepositoryURLError,
    RepositoryNotFoundError,
)

REACT_METADATA = GitHubRepositoryMetadata(
    github_repo_id=10270250,
    full_name="facebook/react",
    default_branch="main",
    description="The library for web and native user interfaces.",
)


def _unique_violation_integrity_error() -> IntegrityError:
    return IntegrityError(
        "duplicate key value violates unique constraint",
        params=None,
        orig=asyncpg.exceptions.UniqueViolationError("duplicate key"),
    )


async def _populate_server_generated_fields(repository: Repository) -> None:
    repository.id = 1
    repository.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Server/column defaults that a real INSERT would apply.
    repository.ingestion_status = "pending"
    repository.ingestion_truncated = False


def test_create_repository_returns_201_on_success(
    client: TestClient, db_session: AsyncMock, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA
    db_session.execute.return_value.scalar_one_or_none.return_value = None
    db_session.refresh.side_effect = _populate_server_generated_fields

    response = client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == 201
    body = response.json()
    assert body["github_repo_id"] == 10270250
    assert body["full_name"] == "facebook/react"
    assert body["default_branch"] == "main"
    assert body["ingestion_status"] == "pending"
    assert body["last_ingested_commit_sha"] is None
    assert body["last_ingested_at"] is None
    assert body["ingestion_error"] is None
    assert body["ingestion_truncated"] is False
    db_session.add.assert_called_once()
    db_session.commit.assert_awaited_once()


@pytest.mark.parametrize(
    ("exception", "expected_status"),
    [
        (InvalidRepositoryURLError("bad url"), 400),
        (RepositoryNotFoundError("not found"), 404),
        (GitHubRateLimitedError("rate limited"), 429),
        (GitHubTimeoutError("timed out"), 504),
        (GitHubUpstreamError("upstream error"), 502),
    ],
)
def test_create_repository_maps_github_errors_to_http_status(
    client: TestClient,
    github_service: AsyncMock,
    exception: Exception,
    expected_status: int,
) -> None:
    github_service.get_repository_metadata.side_effect = exception

    response = client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == expected_status


def test_create_repository_returns_409_when_already_imported(
    client: TestClient, db_session: AsyncMock, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA
    db_session.execute.return_value.scalar_one_or_none.return_value = MagicMock()

    response = client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == 409
    db_session.add.assert_not_called()


def test_create_repository_returns_409_on_unique_violation_race(
    client: TestClient, db_session: AsyncMock, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA
    db_session.execute.return_value.scalar_one_or_none.return_value = None
    db_session.commit.side_effect = _unique_violation_integrity_error()

    response = client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == 409
    db_session.rollback.assert_awaited_once()


def test_create_repository_returns_500_on_unrelated_db_failure(
    client: TestClient, db_session: AsyncMock, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA
    db_session.execute.return_value.scalar_one_or_none.return_value = None
    db_session.commit.side_effect = OperationalError("connection lost", params=None, orig=Exception("boom"))

    response = client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == 500
    assert "boom" not in response.json()["detail"]


def test_create_repository_rejects_empty_url_with_422(client: TestClient) -> None:
    response = client.post("/repositories", json={"github_url": ""})

    assert response.status_code == 422
