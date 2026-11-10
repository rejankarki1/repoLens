"""Curated showcase answers: guests read them without AI or an account, and cannot change anything."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import settings_dep
from apps.api.core.queue import get_queue
from apps.api.main import app
from apps.api.models.entities import Conversation, Message, ShowcaseAnswer, UserRepository
from apps.api.scripts.publish_showcase_answers import plan, publish
from tests.auth_support import make_settings
from tests.search_support import ingest, new_repository

pytestmark = pytest.mark.anyio

SHOWCASE = "octo/showcase"
CITATION = {
    "id": "S1", "chunk_id": 1, "source_file_id": 1, "path": "app.py", "language": "python", "kind": "function",
    "symbol_name": "hash_password", "start_line": 3, "end_line": 9, "start_byte": 10, "end_byte": 90, "commit_sha": "a" * 40,
}


@pytest.fixture(autouse=True)
def isolated(no_auto_membership, app_overrides):
    app.dependency_overrides[settings_dep] = lambda: make_settings(guest_showcase_repositories=SHOWCASE)
    app.dependency_overrides[get_queue] = lambda: MagicMock()
    yield
    for dep in (settings_dep, get_queue):
        app.dependency_overrides.pop(dep, None)


async def answer(db: AsyncSession, repo_id: int, question: str = "How are passwords hashed?", position: int = 1) -> None:
    db.add(ShowcaseAnswer(repository_id=repo_id, position=position, question=question, answer="With scrypt [S1].", citations=[CITATION]))
    await db.commit()


async def test_guests_read_published_answers_of_a_showcase_repository(anon_client, real_db_session) -> None:
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await answer(real_db_session, repo_id, "Second?", position=2)
    await answer(real_db_session, repo_id, "First?", position=1)

    response = await anon_client.get(f"/repositories/{repo_id}/showcase-answers")

    assert response.status_code == 200
    body = response.json()
    assert [a["question"] for a in body] == ["First?", "Second?"]
    assert body[0]["answer"] == "With scrypt [S1]." and body[0]["citations"][0]["path"] == "app.py"


async def test_published_answers_do_not_open_other_repositories_to_guests(anon_client, make_client, real_db_session) -> None:
    alice, a_user = await make_client("alice")
    private = await new_repository(real_db_session, "octo/private")
    await answer(real_db_session, private)
    real_db_session.add(UserRepository(user_id=a_user.id, repository_id=private))
    await real_db_session.commit()
    bob, _b = await make_client("bob")

    assert (await anon_client.get(f"/repositories/{private}/showcase-answers")).status_code == 401
    assert (await bob.get(f"/repositories/{private}/showcase-answers")).status_code == 404
    assert len((await alice.get(f"/repositories/{private}/showcase-answers")).json()) == 1  # its own member


async def test_guests_cannot_change_published_answers(anon_client, make_client, real_db_session) -> None:
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await answer(real_db_session, repo_id)
    bob, _b = await make_client("bob")
    url = f"/repositories/{repo_id}/showcase-answers"

    for client in (anon_client, bob):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = await client.request(method, url, json={"question": "x", "answer": "y"})
            assert response.status_code in (401, 403, 405), (method, response.status_code)
    assert (await real_db_session.execute(select(func.count()).select_from(ShowcaseAnswer))).scalar_one() == 1


# --- the publishing script ----------------------------------------------------------------------------------------

async def conversation(db: AsyncSession, user, repo_id: int, turns: list[tuple[str, str, list]]) -> int:
    conv = Conversation(user_id=user.id, repository_id=repo_id, title="t")
    db.add(conv)
    await db.flush()
    for question, reply, citations in turns:
        db.add(Message(conversation_id=conv.id, role="user", content=question))
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="assistant", content=reply, citations=citations))
        await db.flush()
    await db.commit()
    return conv.id


async def test_publishing_copies_saved_answers_exactly_and_only_those_with_citations(make_client, real_db_session) -> None:
    _alice, user = await make_client("alice")
    repo_id = await new_repository(real_db_session, SHOWCASE)
    await ingest(real_db_session, repo_id)
    conv = await conversation(
        real_db_session, user, repo_id,
        [("How are passwords hashed?", "With scrypt [S1].", [CITATION]), ("Unrelated?", "I found no evidence.", [])],
    )
    settings = make_settings(guest_showcase_repositories=SHOWCASE)

    planned = await plan(real_db_session, settings, "Octo/Showcase", [conv])
    assert [q for q, _a, _c in planned.answers] == ["How are passwords hashed?"] and len(planned.skipped) == 1

    assert await publish(real_db_session, planned, replace=False, apply=False) == 1  # a dry run changes nothing
    assert (await real_db_session.execute(select(func.count()).select_from(ShowcaseAnswer))).scalar_one() == 0

    assert await publish(real_db_session, planned, replace=False, apply=True) == 1
    stored = (await real_db_session.execute(select(ShowcaseAnswer))).scalar_one()
    assert (stored.question, stored.answer, stored.citations) == ("How are passwords hashed?", "With scrypt [S1].", [CITATION])

    assert await publish(real_db_session, planned, replace=False, apply=True) == 0  # repeating it adds no duplicates
    assert await publish(real_db_session, planned, replace=True, apply=True) == 1  # replacing starts over
    assert (await real_db_session.execute(select(func.count()).select_from(ShowcaseAnswer))).scalar_one() == 1


async def test_publishing_refuses_repositories_outside_the_showcase_and_foreign_conversations(make_client, real_db_session) -> None:
    _alice, user = await make_client("alice")
    showcased = await new_repository(real_db_session, SHOWCASE)
    other = await new_repository(real_db_session, "octo/other")
    conv_other = await conversation(real_db_session, user, other, [("Q?", "A [S1].", [CITATION])])
    settings = make_settings(guest_showcase_repositories=SHOWCASE)

    with pytest.raises(SystemExit, match="GUEST_SHOWCASE_REPOSITORIES"):
        await plan(real_db_session, settings, "octo/other", [conv_other])
    with pytest.raises(SystemExit, match="not about"):
        await plan(real_db_session, settings, SHOWCASE, [conv_other])
    assert showcased  # the allow-listed repository itself is untouched by both refusals
