"""Hand the pre-authentication development data to one GitHub user, explicitly.

Before sign-in existed, every conversation and every imported repository belonged to the local development user
(`local@repolens.local`). Nobody can sign in as that user, and no newly registered GitHub user receives its data
automatically. To take it over, sign in with GitHub once, then run (from the repository root):

    python -m apps.api.scripts.claim_local_data --github-login your-login            # shows what would move
    python -m apps.api.scripts.claim_local_data --github-login your-login --apply    # moves it

It moves the conversations (with their messages) and the repository memberships in one transaction. Repositories,
files, embeddings and analyses are shared records and are not touched. Safe to repeat.
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import engine, session_factory
from apps.api.models.entities import Conversation, GitHubAccount, User, UserRepository
from apps.api.services.rag import DEFAULT_USER_EMAIL


async def claim(db: AsyncSession, github_login: str, *, apply: bool) -> dict[str, int | str]:
    local = (await db.execute(select(User).where(User.email == DEFAULT_USER_EMAIL))).scalar_one_or_none()
    accounts = (
        await db.execute(select(GitHubAccount).where(func.lower(GitHubAccount.login) == github_login.lower()))
    ).scalars().all()
    if len(accounts) != 1:
        raise SystemExit(
            f"No GitHub account named {github_login!r} has signed in yet; sign in once first."
            if not accounts
            else f"{len(accounts)} accounts match {github_login!r}; cannot tell which one you mean."
        )
    target_id = accounts[0].user_id
    if local is None:
        return {"target_user_id": target_id, "conversations": 0, "repositories": 0, "note": "no local development data exists"}
    if local.id == target_id:
        raise SystemExit("That account is the local development user itself.")

    conversations = (await db.execute(select(func.count()).select_from(Conversation).where(Conversation.user_id == local.id))).scalar_one()
    repo_ids = list((await db.execute(select(UserRepository.repository_id).where(UserRepository.user_id == local.id))).scalars())
    if apply:
        await db.execute(update(Conversation).where(Conversation.user_id == local.id).values(user_id=target_id))
        if repo_ids:
            await db.execute(
                pg_insert(UserRepository).values([{"user_id": target_id, "repository_id": r} for r in repo_ids]).on_conflict_do_nothing()
            )
        await db.execute(delete(UserRepository).where(UserRepository.user_id == local.id))
        await db.commit()
    else:
        await db.rollback()
    return {"target_user_id": target_id, "conversations": conversations, "repositories": len(repo_ids)}


async def _main(github_login: str, apply: bool) -> None:
    try:
        async with session_factory() as db:
            result = await claim(db, github_login, apply=apply)
    finally:
        await engine.dispose()
    verb = "Moved" if apply else "Would move"
    print(f"{verb} {result['conversations']} conversation(s) and {result['repositories']} repository membership(s) to {github_login}.")
    if result.get("note"):
        print(result["note"])
    if not apply:
        print("Nothing was changed. Re-run with --apply to do it.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--github-login", required=True, help="the GitHub username that should receive the data")
    parser.add_argument("--apply", action="store_true", help="actually move the data (default: only report)")
    args = parser.parse_args()
    asyncio.run(_main(args.github_login, args.apply))


if __name__ == "__main__":
    sys.exit(main())
