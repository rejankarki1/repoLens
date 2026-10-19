"""Tests for GET /repositories/{id}."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from apps.api.models.entities import Repository


def _make_repository() -> Repository:
    return Repository(
        id=1,
        github_repo_id=123456,
        github_account_id=None,
        full_name="octocat/hello-world",
        default_branch="main",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ingestion_status="completed",
        last_ingested_commit_sha="a" * 40,
        ingestion_truncated=False,
        last_ingested_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ingestion_error=None,
    )


def test_get_repository_returns_200_when_found(client: TestClient, db_session: AsyncMock) -> None:
    db_session.execute.return_value.scalar_one_or_none.return_value = _make_repository()

    response = client.get("/repositories/1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["full_name"] == "octocat/hello-world"
    assert body["ingestion_status"] == "completed"
    assert body["last_ingested_commit_sha"] == "a" * 40
    assert body["last_ingested_at"] == "2026-01-02T00:00:00Z"


def test_get_repository_returns_404_when_missing(client: TestClient, db_session: AsyncMock) -> None:
    db_session.execute.return_value.scalar_one_or_none.return_value = None

    response = client.get("/repositories/999")

    assert response.status_code == 404
    assert "999" in response.json()["detail"]


def test_get_repository_returns_422_for_non_integer_id(client: TestClient) -> None:
    response = client.get("/repositories/not-an-id")

    assert response.status_code == 422
