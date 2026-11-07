"""Database side of Architecture Diagram V2: queueing, running, and reading results.

Mirrors `services/architecture/store.py`'s state machine exactly (queued/running/completed/failed attempts;
current/stale results by commit), with one addition: a diagram also depends on the *deterministic*
analysis it was built from, so freshness here checks two things at once — `result_commit_sha` against the
repository's current ingested commit, and `source_analyzer_version` against the deterministic analyzer's
current version. Either going stale makes the stored diagram `stale`, same as the deterministic graph
itself going stale makes `/architecture`'s result stale.

Unlike the deterministic job, this one calls a paid AI provider: `queue_diagram` refuses up front (a fast,
free check) if the deterministic analysis is not itself current, rather than queueing a job that would only
fail once a worker picks it up and spends nothing useful finding that out.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from apps.api.config import Settings, get_settings
from apps.api.models.entities import ArchitectureDiagramRecord, Repository
from apps.api.schemas.architecture_diagram import DIAGRAM_GENERATOR_VERSION, ArchitectureDiagram, DiagramStatus
from apps.api.services.architecture import store as architecture_store
from apps.api.services.architecture_diagram.context import (
    NoArchitectureAnalysisError,
    RepositoryNotFoundError as ContextRepositoryNotFoundError,
    StaleArchitectureAnalysisError,
    load_architecture_context,
)
from apps.api.services.architecture_diagram.generate import build_diagram_generator, generate_architecture_diagram
from apps.api.services.architecture_diagram.validate import DiagramGenerationError
from apps.api.services.generation import GenerationError, Generator

logger = logging.getLogger(__name__)

LOST_JOB_MESSAGE = "The diagram generation stopped unexpectedly. Request it again."
# GenerationError codes worth RQ retrying (a provider hiccup); everything else (bad request, auth, a
# malformed/empty/blocked response) would just fail the same way again, so it is recorded and left alone.
RETRYABLE_GENERATION_CODES = frozenset({"rate_limited", "unavailable", "timeout", "interrupted"})


class RepositoryNotFoundError(Exception):
    pass


class DeterministicAnalysisNotReadyError(Exception):
    """No current deterministic architecture analysis exists yet; nothing to build a diagram from."""


class NoResultError(Exception):
    """No diagram has completed for this repository yet."""


class EnqueueError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- status --------------------------------------------------------------------------------------------------------


def _status(
    repository_id: int, current_sha: str | None, current_analyzer_version: int | None,
    row: ArchitectureDiagramRecord | None, settings: Settings,
) -> DiagramStatus:
    if row is None:
        return DiagramStatus(
            repository_id=repository_id, status="none", freshness="none", current_commit_sha=current_sha,
            generated_commit_sha=None, target_commit_sha=None, generated_at=None, requested_at=None, error=None,
            has_result=False, model=None,
        )
    status, error = row.status, row.error
    if status in ("queued", "running") and row.updated_at < _now() - timedelta(seconds=settings.architecture_stale_seconds):
        status, error = "failed", LOST_JOB_MESSAGE  # reported only; the next request restarts it
    has_result = row.result_commit_sha is not None
    current = (
        has_result
        and row.result_commit_sha == current_sha
        and row.generator_version == DIAGRAM_GENERATOR_VERSION
        and row.source_analyzer_version == current_analyzer_version
    )
    return DiagramStatus(
        repository_id=repository_id, status=status, freshness="current" if current else ("stale" if has_result else "none"),  # type: ignore[arg-type]
        current_commit_sha=current_sha, generated_commit_sha=row.result_commit_sha, target_commit_sha=row.target_commit_sha,
        generated_at=row.generated_at, requested_at=row.requested_at, error=error, has_result=has_result, model=row.model,
    )


async def _repository(db: AsyncSession, repository_id: int) -> Repository:
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise RepositoryNotFoundError(f"Repository {repository_id} not found")
    return repository


async def _row(db: AsyncSession, repository_id: int, *, with_result: bool = False) -> ArchitectureDiagramRecord | None:
    query = select(ArchitectureDiagramRecord).where(ArchitectureDiagramRecord.repository_id == repository_id).execution_options(populate_existing=True)
    if not with_result:
        query = query.options(defer(ArchitectureDiagramRecord.diagram))
    return (await db.execute(query)).scalar_one_or_none()


async def get_status(db: AsyncSession, repository_id: int, settings: Settings | None = None) -> DiagramStatus:
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    return _status(
        repository_id, repository.last_ingested_commit_sha, await _current_analyzer_version(db, repository_id),
        await _row(db, repository_id), settings,
    )


async def _current_analyzer_version(db: AsyncSession, repository_id: int) -> int | None:
    """The deterministic analysis's own `analyzer_version` for its *stored result*, regardless of whether
    that result is itself current — freshness here is about whether our diagram matches what the
    deterministic analysis last successfully produced, which `architecture_store` already tracks."""
    from apps.api.models.entities import ArchitectureAnalysis

    row = (
        await db.execute(select(ArchitectureAnalysis.analyzer_version).where(ArchitectureAnalysis.repository_id == repository_id))
    ).scalar_one_or_none()
    return row


# --- queueing ----------------------------------------------------------------------------------------------------


async def queue_diagram(
    db: AsyncSession, repository_id: int, enqueue: Callable[[], Any], *, force: bool = False, settings: Settings | None = None
) -> tuple[DiagramStatus, bool]:
    """Mark a diagram generation for the repository's current commit as queued and enqueue its job.

    Returns (status, enqueued). Nothing is enqueued when the stored result is already current (unless
    `force`) or a generation of this commit is already queued or running. Raises `RepositoryNotFoundError`,
    `DeterministicAnalysisNotReadyError` (no current deterministic analysis to build from — ask for
    `/architecture` first) or `EnqueueError`.
    """
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    sha = repository.last_ingested_commit_sha
    analysis_status = await architecture_store.get_status(db, repository_id, settings)
    if not sha or analysis_status.freshness != "current":
        raise DeterministicAnalysisNotReadyError(
            "A current deterministic architecture analysis is required before generating a diagram; "
            "request POST /repositories/{id}/architecture first"
        )
    analyzer_version = await _current_analyzer_version(db, repository_id)

    row = await _row(db, repository_id)
    status = _status(repository_id, sha, analyzer_version, row, settings)
    if status.status in ("queued", "running") and status.target_commit_sha == sha:
        return status, False
    if status.freshness == "current" and not force:
        return status, False

    now = _now()
    values = {"status": "queued", "target_commit_sha": sha, "error": None, "requested_at": now, "updated_at": now}
    claimed = await db.execute(
        pg_insert(ArchitectureDiagramRecord).values(repository_id=repository_id, **values)
        .on_conflict_do_update(
            index_elements=["repository_id"], set_=values,
            where=or_(
                ArchitectureDiagramRecord.status.in_(("completed", "failed")),
                ArchitectureDiagramRecord.updated_at < now - timedelta(seconds=settings.architecture_stale_seconds),
                ArchitectureDiagramRecord.target_commit_sha.is_distinct_from(sha),
            ),
        )
        .returning(ArchitectureDiagramRecord.id)
    )
    won = claimed.first() is not None
    await db.commit()
    if not won:
        return await get_status(db, repository_id, settings), False
    try:
        await asyncio.to_thread(enqueue)
    except Exception as exc:
        logger.exception("Could not enqueue diagram generation for repository %s", repository_id)
        await db.execute(
            update(ArchitectureDiagramRecord)
            .where(ArchitectureDiagramRecord.repository_id == repository_id, ArchitectureDiagramRecord.status == "queued")
            .values(status="failed", error="Could not queue the diagram generation job; please try again", updated_at=_now())
        )
        await db.commit()
        raise EnqueueError("Could not queue the diagram generation job") from exc
    return await get_status(db, repository_id, settings), True


# --- running -------------------------------------------------------------------------------------------------------


async def run_diagram(
    sessions: async_sessionmaker[AsyncSession], repository_id: int, *, generator: Generator | None = None, settings: Settings | None = None
) -> str:
    """Generate the repository's diagram and store it. Returns "completed", "failed" or "skipped".

    Every failure mode — the deterministic analysis going stale mid-flight, a provider error, a malformed
    model response — is recorded on the row (the previous result untouched) and returns "failed" rather
    than raising, so a worker crash is the only thing that can turn this into an unhandled exception.
    """
    settings = settings or get_settings()
    async with sessions() as db:
        repository = await db.get(Repository, repository_id)
        sha = repository.last_ingested_commit_sha if repository else None
        if not sha:
            logger.info("Diagram generation skipped for repository %s: nothing ingested", repository_id)
            return "skipped"
        now = _now()
        values = {"status": "running", "target_commit_sha": sha, "error": None, "requested_at": now, "updated_at": now}
        claimed = await db.execute(
            pg_insert(ArchitectureDiagramRecord).values(repository_id=repository_id, **values)
            .on_conflict_do_update(
                index_elements=["repository_id"], set_=values,
                where=or_(
                    ArchitectureDiagramRecord.status != "running",
                    ArchitectureDiagramRecord.updated_at < now - timedelta(seconds=settings.architecture_stale_seconds),
                    ArchitectureDiagramRecord.target_commit_sha.is_distinct_from(sha),
                ),
            )
            .returning(ArchitectureDiagramRecord.id)
        )
        if claimed.first() is None:
            await db.commit()
            logger.info("Diagram generation skipped for repository %s: already running", repository_id)
            return "skipped"
        await db.commit()
        mine = (
            ArchitectureDiagramRecord.repository_id == repository_id,
            ArchitectureDiagramRecord.target_commit_sha == sha,
            ArchitectureDiagramRecord.status == "running",
        )
        try:
            loaded = await load_architecture_context(db, repository_id, settings=settings)
            source_analyzer_version = await _current_analyzer_version(db, repository_id)
            active_generator = generator or build_diagram_generator(settings)
            diagram = await generate_architecture_diagram(loaded, generator=active_generator, settings=settings)
            stored = await db.execute(
                update(ArchitectureDiagramRecord).where(*mine).values(
                    status="completed", error=None, result_commit_sha=sha, generator_version=DIAGRAM_GENERATOR_VERSION,
                    source_analyzer_version=source_analyzer_version, generated_at=_now(), updated_at=_now(),
                    model=getattr(active_generator, "model", None), diagram=diagram.model_dump(mode="json"),
                )
            )
            await db.commit()
            if stored.rowcount == 0:
                logger.info("Diagram result for repository %s discarded: a newer generation is pending", repository_id)
                return "skipped"
            logger.info(
                "Generated diagram for repository %s at %s: %s groups, %s components, %s relationships",
                repository_id, sha[:7], len(diagram.groups), len(diagram.components), len(diagram.relationships),
            )
            return "completed"
        except Exception as exc:
            await db.rollback()
            message = _failure_message(exc)
            if isinstance(exc, GenerationError) and exc.code in RETRYABLE_GENERATION_CODES:
                # Reset to "queued" (not "failed") and re-raise: RQ's Retry policy re-runs this same job
                # after a backoff, and the claim logic above re-enters on `status != "running"` — leaving it
                # "running" here would make the retry's claim attempt silently no-op until the stale timeout.
                logger.warning("Diagram generation for repository %s failed transiently, will retry: %s", repository_id, message)
                await db.execute(update(ArchitectureDiagramRecord).where(*mine).values(status="queued", error=message[:500], updated_at=_now()))
                await db.commit()
                raise
            expected = isinstance(exc, (NoArchitectureAnalysisError, StaleArchitectureAnalysisError, DiagramGenerationError, GenerationError))
            if expected:
                logger.warning("Diagram generation for repository %s failed: %s", repository_id, message)
            else:
                logger.exception("Diagram generation for repository %s failed unexpectedly", repository_id)
            await db.execute(update(ArchitectureDiagramRecord).where(*mine).values(status="failed", error=message[:500], updated_at=_now()))
            await db.commit()
            return "failed"


def _failure_message(exc: Exception) -> str:
    """A safe, specific message for the status row. Known failure modes get a clear explanation; anything
    else (a bug) gets a generic message rather than an internal string that might leak detail."""
    if isinstance(exc, (NoArchitectureAnalysisError, StaleArchitectureAnalysisError, ContextRepositoryNotFoundError)):
        return str(exc)
    if isinstance(exc, GenerationError):
        return f"The AI provider could not generate a diagram: {exc.message}"
    if isinstance(exc, DiagramGenerationError):
        return f"The AI's response could not be used: {exc.message}"
    return f"Diagram generation failed unexpectedly ({type(exc).__name__})"


# --- reading -----------------------------------------------------------------------------------------------------------


async def get_diagram(db: AsyncSession, repository_id: int, settings: Settings | None = None) -> tuple[DiagramStatus, ArchitectureDiagram]:
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    row = await _row(db, repository_id, with_result=True)
    if row is None or row.diagram is None:
        raise NoResultError("No architecture diagram has been generated for this repository yet")
    status = _status(repository_id, repository.last_ingested_commit_sha, await _current_analyzer_version(db, repository_id), row, settings)
    return status, ArchitectureDiagram.model_validate(row.diagram)
