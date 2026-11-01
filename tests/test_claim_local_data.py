"""The explicit hand-over of pre-authentication development data to one GitHub user."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.models.entities import Conversation, User, UserRepository
from apps.api.scripts.claim_local_data import claim
from apps.api.services.rag import DEFAULT_USER_EMAIL
from tests.conftest import make_user
from tests.search_support import new_repository

pytestmark = pytest.mark.anyio


async def seed(db: AsyncSession):
    local = User(email=DEFAULT_USER_EMAIL)
    db.add(local)
    await db.flush()
    repos = [await new_repository(db, f"octo/r{i}") for i in range(2)]
    for r in repos:
        db.add(UserRepository(user_id=local.id, repository_id=r))
    db.add_all([Conversation(user_id=local.id, repository_id=repos[0], title="old chat"), Conversation(user_id=local.id, repository_id=repos[1])])
    await db.commit()
    return local, repos


async def test_nothing_moves_without_apply_and_a_new_user_never_inherits_it(real_db_session: AsyncSession, no_auto_membership) -> None:
    local, repos = await seed(real_db_session)
    local_id = local.id
    newcomer, _t, _c = await make_user(real_db_session, "newcomer")  # signing in alone gives no access to the old data
    assert (await real_db_session.execute(select(UserRepository).where(UserRepository.user_id == newcomer.id))).first() is None

    report = await claim(real_db_session, "newcomer", apply=False)
    assert (report["conversations"], report["repositories"]) == (2, 2)
    owners = (await real_db_session.execute(select(Conversation.user_id))).scalars().all()
    assert set(owners) == {local_id}


async def test_apply_moves_conversations_and_memberships_and_is_repeatable(real_db_session: AsyncSession, no_auto_membership) -> None:
    local, repos = await seed(real_db_session)
    me, _t, _c = await make_user(real_db_session, "me")
    other, _t2, _c2 = await make_user(real_db_session, "someone")
    local_id, me_id, other_id = local.id, me.id, other.id
    real_db_session.add(UserRepository(user_id=other_id, repository_id=repos[0]))
    await real_db_session.commit()

    assert (await claim(real_db_session, "ME", apply=True))["conversations"] == 2  # login match ignores case
    assert set((await real_db_session.execute(select(Conversation.user_id))).scalars().all()) == {me_id}
    mine = (await real_db_session.execute(select(UserRepository.repository_id).where(UserRepository.user_id == me_id))).scalars().all()
    assert sorted(mine) == sorted(repos)
    assert (await real_db_session.execute(select(UserRepository).where(UserRepository.user_id == local_id))).first() is None
    assert (await real_db_session.execute(select(UserRepository).where(UserRepository.user_id == other_id))).scalars().one().repository_id == repos[0]

    again = await claim(real_db_session, "me", apply=True)
    assert (again["conversations"], again["repositories"]) == (0, 0)


async def test_unknown_login_and_missing_local_data_are_reported(real_db_session: AsyncSession) -> None:
    with pytest.raises(SystemExit, match="sign in once first"):
        await claim(real_db_session, "never-signed-in", apply=True)
    await make_user(real_db_session, "solo")
    report = await claim(real_db_session, "solo", apply=True)
    assert report["conversations"] == 0 and "no local development data" in str(report["note"])
