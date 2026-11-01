"""Tests for GET /repositories/{id} (access rules in depth: test_ownership.py)."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.search_support import new_repository

pytestmark = pytest.mark.anyio


async def test_get_repository_returns_200_for_a_member(real_client: httpx.AsyncClient, real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session, "octocat/hello-world")

    response = await real_client.get(f"/repositories/{repo_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == repo_id
    assert body["full_name"] == "octocat/hello-world"
    assert body["ingestion_status"] == "completed"
    assert body["viewer_access"] == "member"


async def test_get_repository_returns_404_when_missing(real_client: httpx.AsyncClient) -> None:
    response = await real_client.get("/repositories/999999")

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


async def test_get_repository_returns_422_for_non_integer_id(real_client: httpx.AsyncClient) -> None:
    assert (await real_client.get("/repositories/not-an-id")).status_code == 422
