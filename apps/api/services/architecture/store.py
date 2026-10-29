"""Database side of architecture analysis: queueing, running, and reading results.

The row in `architecture_analyses` is the single source of truth (Redis only carries the job). Its `status`
describes the latest attempt; the result columns change only when an attempt succeeds, so a failed or lost
re-analysis never hides the last good result. A result describes one commit; if the repository has since been
re-ingested at another commit, readers get it flagged `stale` rather than mixed with the new files.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer

from apps.api.config import Settings, get_settings
from apps.api.models.entities import ArchitectureAnalysis, Repository, SourceFile
from apps.api.schemas.architecture import (
    AnalysisStatus, Cycle, FileExternal, Graph, GraphEdge, GraphNode, NodeDetailResponse, UnresolvedImport,
)
from apps.api.services.architecture.build import ANALYZER_VERSION, SnapshotFile, analyze_snapshot, wanted_content

logger = logging.getLogger(__name__)

_BATCH = 300
LOST_JOB_MESSAGE = "The analysis stopped unexpectedly. Request it again."


class RepositoryNotFoundError(Exception):
    pass


class NotIngestedError(Exception):
    """The repository has no completed ingestion, so there is no snapshot to analyze."""


class NoResultError(Exception):
    """No analysis has completed for this repository yet."""


class NodeNotFoundError(Exception):
    pass


class SnapshotChangedError(Exception):
    """The stored files changed while they were being read (a re-ingestion swapped the snapshot)."""


class EnqueueError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- status --------------------------------------------------------------------------------------------------------


def _status(repository_id: int, current_sha: str | None, row: ArchitectureAnalysis | None, settings: Settings) -> AnalysisStatus:
    if row is None:
        return AnalysisStatus(
            repository_id=repository_id, status="none", freshness="none", current_commit_sha=current_sha, analyzed_commit_sha=None,
            target_commit_sha=None, analyzed_at=None, requested_at=None, error=None, has_result=False,
        )
    status, error = row.status, row.error
    if status in ("queued", "running") and row.updated_at < _now() - timedelta(seconds=settings.architecture_stale_seconds):
        status, error = "failed", LOST_JOB_MESSAGE  # reported only; the next request restarts it
    has_result = row.result_commit_sha is not None
    current = has_result and row.result_commit_sha == current_sha and row.analyzer_version == ANALYZER_VERSION
    return AnalysisStatus(
        repository_id=repository_id, status=status, freshness="current" if current else ("stale" if has_result else "none"),  # type: ignore[arg-type]
        current_commit_sha=current_sha, analyzed_commit_sha=row.result_commit_sha, target_commit_sha=row.target_commit_sha,
        analyzed_at=row.analyzed_at, requested_at=row.requested_at, error=error, has_result=has_result,
    )


async def _repository(db: AsyncSession, repository_id: int) -> Repository:
    repository = await db.get(Repository, repository_id)
    if repository is None:
        raise RepositoryNotFoundError(f"Repository {repository_id} not found")
    return repository


async def _row(db: AsyncSession, repository_id: int, *, with_results: bool = False) -> ArchitectureAnalysis | None:
    # populate_existing: this module changes the row with Core statements, so an instance already in the session's
    # identity map would otherwise be returned with its old attribute values.
    query = select(ArchitectureAnalysis).where(ArchitectureAnalysis.repository_id == repository_id).execution_options(populate_existing=True)
    if not with_results:
        query = query.options(defer(ArchitectureAnalysis.summary), defer(ArchitectureAnalysis.detail))
    return (await db.execute(query)).scalar_one_or_none()


async def get_status(db: AsyncSession, repository_id: int, settings: Settings | None = None) -> AnalysisStatus:
    repository = await _repository(db, repository_id)
    return _status(repository_id, repository.last_ingested_commit_sha, await _row(db, repository_id), settings or get_settings())


# --- queueing ----------------------------------------------------------------------------------------------------------


async def queue_analysis(
    db: AsyncSession, repository_id: int, enqueue: Callable[[], Any], *, force: bool = False, settings: Settings | None = None
) -> tuple[AnalysisStatus, bool]:
    """Mark an analysis of the repository's current commit as queued and enqueue its job.

    Returns (status, enqueued). Nothing is enqueued when the stored result is already current (unless `force`) or
    an analysis of this commit is already queued or running. Raises RepositoryNotFoundError, NotIngestedError or
    EnqueueError (after recording the failure on the row).
    """
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    sha = repository.last_ingested_commit_sha
    if not sha:
        raise NotIngestedError("This repository has not been ingested yet; there are no files to analyze")
    row = await _row(db, repository_id)
    status = _status(repository_id, sha, row, settings)
    if status.status in ("queued", "running") and status.target_commit_sha == sha:
        return status, False
    if status.freshness == "current" and not force:
        return status, False

    now = _now()
    values = {"status": "queued", "target_commit_sha": sha, "error": None, "requested_at": now, "updated_at": now}
    claimed = await db.execute(
        pg_insert(ArchitectureAnalysis).values(repository_id=repository_id, **values)
        .on_conflict_do_update(
            index_elements=["repository_id"], set_=values,
            where=or_(
                ArchitectureAnalysis.status.in_(("completed", "failed")),
                ArchitectureAnalysis.updated_at < now - timedelta(seconds=settings.architecture_stale_seconds),
                ArchitectureAnalysis.target_commit_sha.is_distinct_from(sha),
            ),
        )
        .returning(ArchitectureAnalysis.id)
    )
    won = claimed.first() is not None
    await db.commit()
    if not won:  # another request queued it between our read and write
        return await get_status(db, repository_id, settings), False
    try:
        await asyncio.to_thread(enqueue)
    except Exception as exc:
        logger.exception("Could not enqueue architecture analysis for repository %s", repository_id)
        await db.execute(
            update(ArchitectureAnalysis)
            .where(ArchitectureAnalysis.repository_id == repository_id, ArchitectureAnalysis.status == "queued")
            .values(status="failed", error="Could not queue the analysis job; please try again", updated_at=_now())
        )
        await db.commit()
        raise EnqueueError("Could not queue the analysis job") from exc
    return await get_status(db, repository_id, settings), True


# --- running -----------------------------------------------------------------------------------------------------------


async def load_snapshot(db: AsyncSession, repository_id: int, commit_sha: str) -> list[SnapshotFile]:
    """Every stored file of exactly this commit; the text only of the files the analysis reads.

    Raises SnapshotChangedError if rows disappear while reading (a re-ingestion deleted this commit's files), so a
    result can never mix two commits.
    """
    scope = (SourceFile.repository_id == repository_id, SourceFile.commit_sha == commit_sha)
    rows = (await db.execute(select(SourceFile.path, SourceFile.category, SourceFile.size_bytes).where(*scope))).all()
    files = {path: SnapshotFile(path, category, size, None) for path, category, size in rows}
    wanted = sorted(p for p, f in files.items() if wanted_content(p, f.size_bytes))
    loaded = 0
    for start in range(0, len(wanted), _BATCH):
        batch = wanted[start: start + _BATCH]
        for path, content in (await db.execute(select(SourceFile.path, SourceFile.content).where(*scope, SourceFile.path.in_(batch)))).all():
            files[path].content = content
            loaded += 1
    total = (await db.execute(select(func.count()).select_from(SourceFile).where(*scope))).scalar_one()
    if loaded != len(wanted) or total != len(files):
        raise SnapshotChangedError(f"The stored files of commit {commit_sha[:7]} changed during the analysis")
    return [files[p] for p in sorted(files)]


async def run_analysis(sessions: async_sessionmaker[AsyncSession], repository_id: int, settings: Settings | None = None) -> str:
    """Analyze the repository's current commit and store the result. Returns "completed", "failed" or "skipped".

    Failures are recorded on the row (the previous result is untouched) and not raised: the analysis is
    deterministic, so retrying the job would fail the same way. The user can request it again.
    """
    settings = settings or get_settings()
    async with sessions() as db:
        repository = await db.get(Repository, repository_id)
        sha = repository.last_ingested_commit_sha if repository else None
        if not sha:
            logger.info("Architecture analysis skipped for repository %s: nothing ingested", repository_id)
            return "skipped"
        now = _now()
        values = {"status": "running", "target_commit_sha": sha, "error": None, "requested_at": now, "updated_at": now}
        claimed = await db.execute(
            pg_insert(ArchitectureAnalysis).values(repository_id=repository_id, **values)
            .on_conflict_do_update(
                index_elements=["repository_id"], set_=values,
                where=or_(
                    ArchitectureAnalysis.status != "running",
                    ArchitectureAnalysis.updated_at < now - timedelta(seconds=settings.architecture_stale_seconds),
                    ArchitectureAnalysis.target_commit_sha.is_distinct_from(sha),
                ),
            )
            .returning(ArchitectureAnalysis.id)
        )
        if claimed.first() is None:
            await db.commit()
            logger.info("Architecture analysis skipped for repository %s: already running", repository_id)
            return "skipped"
        await db.commit()
        mine = (ArchitectureAnalysis.repository_id == repository_id, ArchitectureAnalysis.target_commit_sha == sha, ArchitectureAnalysis.status == "running")
        try:
            files = await load_snapshot(db, repository_id, sha)
            result = await asyncio.to_thread(analyze_snapshot, sha, files, max_files=settings.architecture_max_files)
            stored = await db.execute(
                update(ArchitectureAnalysis).where(*mine).values(
                    status="completed", error=None, result_commit_sha=sha, analyzer_version=ANALYZER_VERSION, analyzed_at=_now(),
                    updated_at=_now(), summary=result.graph.model_dump(mode="json"), detail=result.detail,
                )
            )
            await db.commit()
            if stored.rowcount == 0:  # a newer request took over this row; its own job writes the result
                logger.info("Architecture result for repository %s discarded: a newer analysis is pending", repository_id)
                return "skipped"
            logger.info(
                "Analyzed repository %s at %s: %s files, %s file edges, %s modules",
                repository_id, sha[:7], result.graph.stats.code_files, result.graph.stats.file_edges, result.graph.stats.modules,
            )
            return "completed"
        except Exception as exc:
            await db.rollback()
            logger.exception("Architecture analysis of repository %s failed", repository_id)
            message = str(exc) if isinstance(exc, SnapshotChangedError) else f"The analysis failed unexpectedly ({type(exc).__name__})"
            await db.execute(update(ArchitectureAnalysis).where(*mine).values(status="failed", error=message[:500], updated_at=_now()))
            await db.commit()
            return "failed"


# --- reading -----------------------------------------------------------------------------------------------------------


async def get_overview(db: AsyncSession, repository_id: int, settings: Settings | None = None) -> tuple[AnalysisStatus, Graph]:
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    row = await _row(db, repository_id, with_results=True)
    if row is None or row.summary is None:
        raise NoResultError("No architecture analysis has completed for this repository yet")
    return _status(repository_id, repository.last_ingested_commit_sha, row, settings), Graph.model_validate(row.summary)


async def get_node_detail(db: AsyncSession, repository_id: int, node_id: str, settings: Settings | None = None) -> NodeDetailResponse:
    settings = settings or get_settings()
    repository = await _repository(db, repository_id)
    row = await _row(db, repository_id, with_results=True)
    if row is None or row.summary is None or row.detail is None:
        raise NoResultError("No architecture analysis has completed for this repository yet")
    status = _status(repository_id, repository.last_ingested_commit_sha, row, settings)
    graph = Graph.model_validate(row.summary)
    file_nodes = {n["id"]: GraphNode.model_validate(n) for n in row.detail["nodes"]}
    edges = [GraphEdge.model_validate(e) for e in row.detail["edges"]]
    module_of: dict[str, str] = row.detail["module_of"]

    if node_id.startswith("module:"):
        node = next((n for n in graph.nodes if n.id == node_id), None)
        if node is None:
            raise NodeNotFoundError(f"No module {node_id!r} in this analysis")
        member_ids = {fid for fid, mid in module_of.items() if mid == node_id}
    elif node_id.startswith("file:") and node_id in file_nodes:
        node, member_ids = file_nodes[node_id], {node_id}
    else:
        raise NodeNotFoundError(f"No node {node_id!r} in this analysis")

    is_module = node.type == "module"
    internal = [e for e in edges if e.source in member_ids and e.target in member_ids] if is_module else []
    outgoing = [e for e in edges if e.source in member_ids and e.target not in member_ids]
    incoming = [e for e in edges if e.target in member_ids and e.source not in member_ids]
    related_ids = {e.target for e in outgoing} | {e.source for e in incoming}
    member_paths = {file_nodes[m].path for m in member_ids}
    members_nodes = [file_nodes[m] for m in member_ids] if is_module else []
    return NodeDetailResponse(
        analysis=status, node=node,
        members=sorted((file_nodes[m] for m in member_ids), key=lambda n: n.id) if is_module else [],
        internal_edges=internal, outgoing=outgoing, incoming=incoming,
        related_nodes=sorted((file_nodes[r] for r in related_ids if r in file_nodes), key=lambda n: n.id),
        external_dependencies=[FileExternal.model_validate(x) for x in row.detail["externals"] if x["file"] in member_paths],
        unresolved_imports=[UnresolvedImport.model_validate(u) for u in row.detail["unresolved"] if u["file"] in member_paths],
        cycles=[c for c in graph.cycles if c.id in {n.cycle_id for n in ([node] + members_nodes)}],
    )
