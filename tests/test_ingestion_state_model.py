"""Model-level tests for the `queued` ingestion status and `ingestion_heartbeat_at` column.

These run against the disposable `repolens_test` database, whose schema is created from the
SQLAlchemy models, so they verify the CHECK constraint and column exactly as the models declare them.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import CheckConstraint, DateTime, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Repository

pytestmark = pytest.mark.anyio

_github_ids = iter(range(400_000, 500_000))


def _repository(**fields: object) -> Repository:
    return Repository(github_repo_id=next(_github_ids), full_name="octo/demo", default_branch="main", **fields)


async def test_new_repository_has_no_heartbeat_and_is_pending(real_db_session: AsyncSession) -> None:
    repo = _repository()
    real_db_session.add(repo)
    await real_db_session.commit()

    stored = (
        await real_db_session.execute(select(Repository).where(Repository.id == repo.id).execution_options(populate_existing=True))
    ).scalar_one()
    assert stored.ingestion_status == "pending"
    assert stored.ingestion_heartbeat_at is None


@pytest.mark.parametrize("status", ["pending", "queued", "in_progress", "completed", "failed"])
async def test_every_ingestion_status_including_queued_is_accepted(real_db_session: AsyncSession, status: str) -> None:
    repo = _repository(ingestion_status=status)
    real_db_session.add(repo)
    await real_db_session.commit()

    assert repo.id is not None


async def test_unknown_ingestion_status_is_rejected(real_db_session: AsyncSession) -> None:
    real_db_session.add(_repository(ingestion_status="running"))

    with pytest.raises(IntegrityError):
        await real_db_session.commit()
    await real_db_session.rollback()


async def test_heartbeat_round_trips_as_timezone_aware(real_db_session: AsyncSession) -> None:
    beat = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    repo = _repository(ingestion_status="in_progress", ingestion_heartbeat_at=beat)
    real_db_session.add(repo)
    await real_db_session.commit()

    stored = (
        await real_db_session.execute(select(Repository).where(Repository.id == repo.id).execution_options(populate_existing=True))
    ).scalar_one()
    assert stored.ingestion_heartbeat_at == beat
    assert stored.ingestion_heartbeat_at.utcoffset() == timedelta(0)

    stored.ingestion_heartbeat_at = None  # nullable: cleared when an ingestion ends
    await real_db_session.commit()
    assert repo.ingestion_heartbeat_at is None


def test_model_declares_the_expected_constraint_and_column() -> None:
    table = Repository.__table__
    check = next(c for c in table.constraints if isinstance(c, CheckConstraint) and c.name == "valid_ingestion_status")
    assert "'queued'" in str(check.sqltext)
    column = table.c.ingestion_heartbeat_at
    assert column.nullable is True
    assert isinstance(column.type, DateTime) and column.type.timezone is True
    assert column.server_default is None
