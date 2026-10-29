"""Repository-scoped code search endpoints."""

import asyncio
import logging
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.core.database import get_db
from apps.api.core.queue import EMBEDDING_JOB_FUNC, enqueue_embedding, get_queue, live_repository_ids
from apps.api.schemas.search import IndexStatusResponse, SearchHitResponse, SearchResponse
from apps.api.services.code_search import (
    NotIndexedError,
    RepositoryNotFoundError,
    SemanticUnavailableError,
    index_status,
    search_repository,
)
from apps.api.services.embeddings import GeminiEmbedder, get_embedder

logger = logging.getLogger(__name__)
router = APIRouter(tags=["search"])


@router.get("/repositories/{repository_id}/search", response_model=SearchResponse, status_code=200)
async def search_code(
    repository_id: int,
    q: str = Query(min_length=1, max_length=500, description="A symbol, identifier, file name or question"),
    limit: int = Query(10, ge=1, le=50),
    mode: Literal["hybrid", "keyword", "semantic"] = Query("hybrid"),
    db: AsyncSession = Depends(get_db),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
) -> SearchResponse:
    """Search the repository's current snapshot. `hybrid` combines keyword and semantic results and
    quietly falls back to keyword-only (with a warning) when semantic search is unavailable."""
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="The search query must not be empty")
    try:
        outcome = await search_repository(db, repository_id, query, limit=limit, mode=mode, embedder=embedder)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotIndexedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SemanticUnavailableError as exc:
        raise HTTPException(status_code=503 if exc.temporary else 409, detail=str(exc)) from exc
    return SearchResponse(
        query=outcome.query,
        mode=outcome.mode,
        hits=[SearchHitResponse(**asdict(hit), matched_by=hit.matched_by) for hit in outcome.hits],
        warnings=outcome.warnings,
        index=IndexStatusResponse(**asdict(outcome.index)),
        took_ms=round(outcome.took_ms, 1),
    )


@router.get("/repositories/{repository_id}/search-index", response_model=IndexStatusResponse, status_code=200)
async def get_search_index(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
) -> IndexStatusResponse:
    """Source-ingestion and embedding-index readiness, kept separate on purpose."""
    try:
        status = await index_status(db, repository_id, embeddings_enabled=embedder is not None)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IndexStatusResponse(**asdict(status))


@router.post("/repositories/{repository_id}/embeddings", response_model=IndexStatusResponse, status_code=202)
async def build_search_index(
    repository_id: int,
    db: AsyncSession = Depends(get_db),
    queue: Queue = Depends(get_queue),
    embedder: GeminiEmbedder | None = Depends(get_embedder),
) -> IndexStatusResponse:
    """Queue the background job that embeds this repository's missing chunks (safe to repeat)."""
    try:
        status = await index_status(db, repository_id, embeddings_enabled=embedder is not None)
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if embedder is None:
        raise HTTPException(status_code=409, detail="Semantic search is not configured (set GEMINI_API_KEY)")
    if status.chunks == 0:
        raise HTTPException(status_code=409, detail="This repository has no code chunks to embed; ingest it first")
    if status.embedding_job_running:
        raise HTTPException(status_code=409, detail="An embedding job is already running for this repository")
    try:
        pending = await asyncio.to_thread(live_repository_ids, queue, None, EMBEDDING_JOB_FUNC)
    except Exception:  # best effort: if RQ cannot be read, the enqueue below will report Redis problems
        pending = set()
    if repository_id in pending:
        raise HTTPException(status_code=409, detail="An embedding job is already queued or waiting to retry")
    try:
        await asyncio.to_thread(enqueue_embedding, queue, repository_id, get_settings())
    except Exception as exc:
        logger.exception("Could not enqueue embeddings for repository %s", repository_id)
        raise HTTPException(status_code=503, detail="Could not queue the embedding job; please try again") from exc
    return IndexStatusResponse(**asdict(status))
