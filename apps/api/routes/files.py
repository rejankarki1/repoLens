"""Read-only endpoints for browsing a repository's stored source files."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.models.entities import Repository, SourceFile
from apps.api.schemas.file import FileContent, FileSummary

router = APIRouter(tags=["files"])


async def _current_commit_sha(db: AsyncSession, repository_id: int) -> str | None:
    """Return the repository's last successfully ingested commit (None if never ingested).

    Raises 404 if the repository doesn't exist. Files are only ever read for this commit, so a
    failed or in-progress re-ingestion never exposes partial data: the previous snapshot stays current.
    """
    row = (
        await db.execute(
            select(Repository.id, Repository.last_ingested_commit_sha).where(Repository.id == repository_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Repository {repository_id} not found")
    return row.last_ingested_commit_sha


@router.get("/repositories/{repository_id}/files", response_model=list[FileSummary], status_code=200)
async def list_files(repository_id: int, db: AsyncSession = Depends(get_db)) -> list[FileSummary]:
    """List the current snapshot's files (metadata only), sorted by path."""
    commit_sha = await _current_commit_sha(db, repository_id)
    if commit_sha is None:
        return []
    rows = await db.execute(
        select(
            SourceFile.path,
            SourceFile.language,
            SourceFile.category,
            SourceFile.size_bytes,
            SourceFile.line_count,
        )
        .where(SourceFile.repository_id == repository_id, SourceFile.commit_sha == commit_sha)
        .order_by(SourceFile.path.collate("C"))
    )
    return [FileSummary.model_validate(row) for row in rows.all()]


@router.get("/repositories/{repository_id}/files/content", response_model=FileContent, status_code=200)
async def get_file_content(
    repository_id: int,
    path: str = Query(min_length=1, description="Repository-relative file path, e.g. src/app.py"),
    db: AsyncSession = Depends(get_db),
) -> FileContent:
    """Return one file's stored source text from the current snapshot."""
    commit_sha = await _current_commit_sha(db, repository_id)
    row = None
    if commit_sha is not None:
        row = (
            await db.execute(
                select(
                    SourceFile.path,
                    SourceFile.language,
                    SourceFile.category,
                    SourceFile.size_bytes,
                    SourceFile.line_count,
                    SourceFile.content,
                ).where(
                    SourceFile.repository_id == repository_id,
                    SourceFile.commit_sha == commit_sha,
                    SourceFile.path == path,
                )
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"File {path!r} not found in repository {repository_id}")
    return FileContent.model_validate(row)
