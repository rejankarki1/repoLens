"""`load_architecture_context` against the real test database: reuses the real ingestion/analysis pipeline
(no shortcuts), reads only existing stored data, and never touches an AI provider."""

import hashlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import get_settings
from apps.api.models.entities import Repository
from apps.api.services.architecture import store as architecture_store
from apps.api.services.architecture_diagram.context import (
    NoArchitectureAnalysisError,
    RepositoryNotFoundError,
    StaleArchitectureAnalysisError,
    load_architecture_context,
)
from apps.api.services.archive_reader import ArchiveReadResult, Category
from apps.api.services.archive_reader import SourceFile as ArchiveFile
from apps.api.services.code_indexing import index_entries
from apps.api.services.source_file_persistence import persist_source_snapshot
from tests.search_support import new_repository

pytestmark = pytest.mark.anyio

SHA_A, SHA_B = "a" * 40, "b" * 40

FILES = {
    "pyproject.toml": ('[project]\nname = "demo"\ndependencies = ["fastapi"]\n', "toml", Category.CONFIG),
    "apps/api/routes/users.py": (
        "from fastapi import APIRouter\n\nfrom apps.api.services import accounts\n\n"
        "router = APIRouter()\n\n\n@router.get(\"/users\")\nasync def list_users():\n    return accounts.all_users()\n",
        "python", Category.SOURCE,
    ),
    "apps/api/services/accounts.py": "def all_users():\n    return []\n",
    "tests/test_users.py": ("from apps.api.routes.users import router\n\n\ndef test_router():\n    assert router\n", "python", Category.TEST),
    "README.md": ("# Demo\n\nA tiny FastAPI service that serves a list of users.\n", "markdown", Category.DOCS),
}
# the plain-string entry above has no explicit category; normalize it here rather than repeating SOURCE everywhere
FILES["apps/api/services/accounts.py"] = (FILES["apps/api/services/accounts.py"], "python", Category.SOURCE)


def _snapshot(sha: str) -> ArchiveReadResult:
    entries = tuple(
        ArchiveFile(
            path=path, language=language, category=category, content=content,
            sha256=hashlib.sha256(content.encode()).hexdigest(), size_bytes=len(content.encode()), line_count=content.count("\n"),
        )
        for path, (content, language, category) in FILES.items()
    )
    return ArchiveReadResult(sha, entries, (), {}, False, None, 0)


async def _ingest(db: AsyncSession, repo_id: int, sha: str = SHA_A) -> None:
    result = _snapshot(sha)
    await persist_source_snapshot(db, repo_id, result, index_entries(result.entries, chunk_max_bytes=220).chunks_by_path)


@asynccontextmanager
async def _same_session(db: AsyncSession):
    yield db


async def _analyze(db: AsyncSession, repo_id: int) -> str:
    # run_analysis takes a sessionmaker-shaped callable; reuse the single test session for every call it makes
    # (same pattern as tests/test_architecture_api.py's `env` fixture).
    return await architecture_store.run_analysis(lambda: _same_session(db), repo_id)


async def test_loads_modules_readme_and_manifest_from_a_real_analysis(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session, "octo/loader-demo")
    await _ingest(real_db_session, repo_id)
    outcome = await _analyze(real_db_session, repo_id)
    assert outcome == "completed"

    loaded = await load_architecture_context(real_db_session, repo_id)
    assert loaded.commit_sha == SHA_A
    assert loaded.repo_full_name == "octo/loader-demo"
    assert any(m.path == "apps/api" or m.path.startswith("apps") for m in loaded.context.modules)
    assert loaded.context.readme_excerpt is not None and "FastAPI service" in loaded.context.readme_excerpt
    assert any(d.path == "pyproject.toml" for d in loaded.context.manifests)
    assert "apps/api/routes/users.py" in loaded.known_paths
    assert loaded.module_of.get("tests/test_users.py", "").startswith("module:")
    # the real cross-module import edge (tests -> apps) must be visible in the selected context
    assert any(e.source == loaded.module_of["tests/test_users.py"] for e in loaded.context.module_edges)


async def test_missing_analysis_raises_a_clear_error(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session, "octo/loader-no-analysis")
    await _ingest(real_db_session, repo_id)
    with pytest.raises(NoArchitectureAnalysisError):
        await load_architecture_context(real_db_session, repo_id)


async def test_stale_analysis_is_refused_rather_than_mixed_with_newer_files(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session, "octo/loader-stale")
    await _ingest(real_db_session, repo_id, sha=SHA_A)
    assert await _analyze(real_db_session, repo_id) == "completed"

    # simulate a re-ingestion at a new commit without re-running the architecture analysis
    await _ingest(real_db_session, repo_id, sha=SHA_B)
    repo = await real_db_session.get(Repository, repo_id)
    assert repo.last_ingested_commit_sha == SHA_B

    with pytest.raises(StaleArchitectureAnalysisError):
        await load_architecture_context(real_db_session, repo_id)


async def test_unknown_repository_raises_a_clear_error(real_db_session: AsyncSession) -> None:
    with pytest.raises(RepositoryNotFoundError):
        await load_architecture_context(real_db_session, 999_999_999)
