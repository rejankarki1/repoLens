"""Publish real, already-saved Ask AI answers as the example answers visitors see for a showcase repository.

Sign in as yourself, add the showcase repository to your workspace (so it is ingested and indexed), ask a few good
questions in Ask AI, and note the conversation ids (the `c=` number in the address bar). Then, from the repository root:

    python -m apps.api.scripts.publish_showcase_answers --repo owner/name --conversation 12 --conversation 13            # shows what would be published
    python -m apps.api.scripts.publish_showcase_answers --repo owner/name --conversation 12 --conversation 13 --apply    # publishes it

The answers and their validated citations are copied exactly as saved (nothing is generated or edited here), only
for repositories listed in `GUEST_SHOWCASE_REPOSITORIES`, and only question/answer pairs that have citations.
`--replace` removes the repository's previously published answers first. Visitors read them without signing in and
without any AI call.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.core.database import engine, session_factory
from apps.api.models.entities import Conversation, Message, Repository, ShowcaseAnswer
from apps.api.services.access import showcase_names


@dataclass
class PublishPlan:
    repository_id: int
    answers: list[tuple[str, str, list[dict]]]  # question, answer, citations
    skipped: list[str]  # reasons, for the report


async def plan(db: AsyncSession, settings: Settings, full_name: str, conversation_ids: list[int]) -> PublishPlan:
    if full_name.lower() not in showcase_names(settings):
        raise SystemExit(f"{full_name} is not in GUEST_SHOWCASE_REPOSITORIES, so nothing may be published for it.")
    repository = (await db.execute(select(Repository).where(func.lower(Repository.full_name) == full_name.lower()))).scalar_one_or_none()
    if repository is None:
        raise SystemExit(f"{full_name} has not been imported yet.")

    answers: list[tuple[str, str, list[dict]]] = []
    skipped: list[str] = []
    for conversation_id in conversation_ids:
        conversation = await db.get(Conversation, conversation_id)
        if conversation is None or conversation.repository_id != repository.id:
            raise SystemExit(f"Conversation {conversation_id} does not exist or is not about {full_name}.")
        messages = (await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id))).scalars().all()
        question: Message | None = None
        for message in messages:
            if message.role == "user":
                question = message
            elif message.role == "assistant" and question is not None:
                if message.citations:
                    answers.append((question.content, message.content, list(message.citations)))
                else:
                    skipped.append(f"conversation {conversation_id}: no citations for {question.content[:60]!r}")
                question = None
    return PublishPlan(repository.id, answers, skipped)


async def publish(db: AsyncSession, publish_plan: PublishPlan, *, replace: bool, apply: bool) -> int:
    if not apply:
        await db.rollback()
        return len(publish_plan.answers)
    if replace:
        await db.execute(delete(ShowcaseAnswer).where(ShowcaseAnswer.repository_id == publish_plan.repository_id))
    position = (
        await db.execute(select(func.coalesce(func.max(ShowcaseAnswer.position), 0)).where(ShowcaseAnswer.repository_id == publish_plan.repository_id))
    ).scalar_one()
    existing = set(
        (await db.execute(select(ShowcaseAnswer.question).where(ShowcaseAnswer.repository_id == publish_plan.repository_id))).scalars()
    )
    added = 0
    for question, answer, citations in publish_plan.answers:
        if question in existing:
            continue
        position += 1
        db.add(ShowcaseAnswer(repository_id=publish_plan.repository_id, position=position, question=question, answer=answer, citations=citations))
        existing.add(question)
        added += 1
    await db.commit()
    return added


async def _main(full_name: str, conversation_ids: list[int], replace: bool, apply: bool) -> None:
    try:
        async with session_factory() as db:
            publish_plan = await plan(db, get_settings(), full_name, conversation_ids)
            count = await publish(db, publish_plan, replace=replace, apply=apply)
    finally:
        await engine.dispose()
    for reason in publish_plan.skipped:
        print(f"Skipped {reason}")
    print(f"{'Published' if apply else 'Would publish'} {count} answer(s) for {full_name}.")
    if not apply:
        print("Nothing was changed. Re-run with --apply to publish.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="the showcase repository, owner/name")
    parser.add_argument("--conversation", type=int, action="append", required=True, help="a saved conversation about it (repeatable)")
    parser.add_argument("--replace", action="store_true", help="remove the repository's published answers first")
    parser.add_argument("--apply", action="store_true", help="actually publish (default: only report)")
    args = parser.parse_args()
    asyncio.run(_main(args.repo, args.conversation, args.replace, args.apply))


if __name__ == "__main__":
    sys.exit(main())
