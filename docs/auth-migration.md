# Authentication migration notes

How to move an existing (Phase 1, single-user) database to the authentication schema without losing anything.

## What changes

Two additive Alembic migrations. Neither deletes, rewrites or reassigns an existing row.

| Revision | Effect |
| --- | --- |
| `c1a7e3d9f402` | Creates `sessions`. Adds `display_name`, `avatar_url`, `created_at`, `last_login_at` to `github_accounts`. Makes `users.email` nullable. |
| `d2b8f4e0a513` | Creates `user_repositories`. If the local development user `local@repolens.local` exists, inserts one membership row per existing repository for that user. |

Repositories, source files, chunks, embeddings, architecture analyses, conversations and messages are untouched.

## Upgrading a development database

Take a backup first (cheap insurance for any migration):

```bash
docker compose exec -T postgres pg_dump -U repolens -d repolens > repolens-backup.sql
apps/api/venv/bin/alembic upgrade head
apps/api/venv/bin/alembic current        # d2b8f4e0a513 (head)
```

After the upgrade nobody is signed in and the local development user cannot sign in, so the dashboard is empty until you sign in with GitHub. Your old data is still there; claim it as below.

## Taking over the old development data

1. Sign in with GitHub once (this creates your user).
2. Preview, then apply:

```bash
apps/api/venv/bin/python -m apps.api.scripts.claim_local_data --github-login <your-login>
apps/api/venv/bin/python -m apps.api.scripts.claim_local_data --github-login <your-login> --apply
```

The command moves the local user's conversations (their messages follow) and repository memberships to your account in a single transaction. The default is a dry run. It can be repeated safely, and it refuses to guess if the login is unknown or ambiguous. Nothing is ever assigned to a newly registered user automatically; this command is the only path.

## Production

A new production database has no local user, so the backfill inserts nothing. Run `alembic upgrade head` as part of each release (see `docs/release-baseline.md`) and never run the claim command there.

## Downgrading

`alembic downgrade b84f2c6d1e07` drops `sessions` and `user_repositories` (sign-in state and workspace memberships are lost; repositories and conversations stay). It stops with an error, changing nothing, if any user without an email exists, because making `users.email` required again would mean deleting or inventing data. Remove or give an email to those users deliberately first.

## How it was checked

- `tests/test_auth_migrations.py` runs the real migrations on disposable databases: from empty; from a seeded Phase 1 schema (data preserved, only the local user made a member); and it exercises the new constraints, indexes and cascade behaviour (deleting a repository removes memberships and keeps conversations; deleting a user removes their sessions, conversations and GitHub account).
- The upgrade, the claim command (dry run, apply, repeat) and a downgrade were rehearsed on a copy of a real development database. The original was not modified.
