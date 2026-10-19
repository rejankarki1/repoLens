"""Integration tests for POST /repositories against a real, isolated Postgres database.

The database is real (the `repolens_test` database created by the `real_db_session`/`real_client`
fixtures in conftest.py). Only the external GitHub HTTP call is mocked - no live network requests.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Repository
from apps.api.services.github import GitHubRepositoryMetadata

pytestmark = pytest.mark.anyio

REACT_METADATA = GitHubRepositoryMetadata(
    github_repo_id=10270250,
    full_name="facebook/react",
    default_branch="main",
    description="The library for web and native user interfaces.",
)


async def test_create_repository_persists_a_real_row(
    real_client: httpx.AsyncClient, real_db_session: AsyncSession, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA

    response = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert response.status_code == 201
    created_id = response.json()["id"]

    result = await real_db_session.execute(select(Repository).where(Repository.id == created_id))
    row = result.scalar_one()
    assert row.github_repo_id == 10270250
    assert row.full_name == "facebook/react"
    assert row.default_branch == "main"


async def test_create_repository_returns_409_on_real_duplicate(
    real_client: httpx.AsyncClient, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA

    first = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})
    second = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert first.status_code == 201
    assert second.status_code == 409


async def test_imported_repository_is_retrievable_via_get_endpoints(
    real_client: httpx.AsyncClient, github_service: AsyncMock
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA

    created = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})
    repository_id = created.json()["id"]

    get_one = await real_client.get(f"/repositories/{repository_id}")
    get_all = await real_client.get("/repositories")

    assert get_one.status_code == 200
    assert get_one.json()["full_name"] == "facebook/react"
    assert any(repo["id"] == repository_id for repo in get_all.json())
