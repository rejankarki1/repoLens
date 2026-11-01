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


async def test_create_repository_twice_reuses_the_row(
    real_client: httpx.AsyncClient, github_service: AsyncMock, real_db_session: AsyncSession
) -> None:
    github_service.get_repository_metadata.return_value = REACT_METADATA

    first = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})
    second = await real_client.post("/repositories", json={"github_url": "https://github.com/facebook/react"})

    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"]
    rows = await real_db_session.execute(select(Repository).where(Repository.github_repo_id == 10270250))
    assert len(rows.scalars().all()) == 1
