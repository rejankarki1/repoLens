"""Search and embedding-index tests against the disposable `repolens_test` database.

The embedding provider is always a deterministic fake (no network, no key). The tests check: exact
symbol, identifier, path and natural-language search, citation accuracy, repository and commit
isolation, the keyword-only fallback, embedding failures, and index replacement on re-ingestion.
"""

import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from apps.api.config import Settings, get_settings
from apps.api.jobs.embedding_job import TransientEmbeddingError, run_embedding
from apps.api.models.entities import CodeChunk, CodeEmbedding, Repository, SourceFile
from apps.api.services.code_search import (
    NotIndexedError,
    RepositoryNotFoundError,
    SemanticUnavailableError,
    index_status,
    search_repository,
)
from apps.api.services.embeddings import EmbeddingProviderError, build_embeddings, embedding_lock
from tests.search_support import (
    CORPUS,
    SHA_A,
    SHA_B,
    SHA_OLD,
    FakeEmbedder,
    ingest,
    new_repository,
    snapshot,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
async def engine(_prepared_test_database: None) -> AsyncIterator[AsyncEngine]:
    """A real engine on the test database, used for the advisory-lock connection only."""
    url = make_url(get_settings().database_url).set(database="repolens_test")
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


def _sessions(db: AsyncSession):  # type: ignore[no-untyped-def]
    """Embedding jobs open their own sessions; in tests they share the transactional test session."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db

    return factory


async def _embed(engine: AsyncEngine, db: AsyncSession, repo_id: int, embedder: FakeEmbedder, **settings: object):  # type: ignore[no-untyped-def]
    return await build_embeddings(engine, _sessions(db), repo_id, embedder, Settings(**settings))  # type: ignore[arg-type]


async def _search(db: AsyncSession, repo_id: int, query: str, **kwargs: object):  # type: ignore[no-untyped-def]
    return await search_repository(db, repo_id, query, **kwargs)  # type: ignore[arg-type]


async def _file_content(db: AsyncSession, source_file_id: int) -> str:
    return (await db.execute(select(SourceFile.content).where(SourceFile.id == source_file_id))).scalar_one()


# --- keyword search -----------------------------------------------------------------------------------


async def test_every_stored_chunk_has_a_search_vector(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    total, indexed = (
        await real_db_session.execute(select(func.count(), func.count(CodeChunk.search_vector)).select_from(CodeChunk))
    ).one()

    assert total > 4 and indexed == total


@pytest.mark.parametrize(
    ("query", "path", "symbol"),
    [
        ("loadEnvLocal", "scripts/env.ts", "loadEnvLocal"),  # exact identifier
        ("load env local", "scripts/env.ts", "loadEnvLocal"),  # the words of a camelCase name
        ("load_env_local", "scripts/env.ts", "loadEnvLocal"),  # snake_case spelling of it
        ("loadEnv", "scripts/env.ts", "loadEnvLocal"),  # identifier prefix
        ("verify_password", "backend/auth.py", "verify_password"),
        ("SessionStore", "backend/auth.py", "SessionStore"),
        ("AppButton", "src/components/ui/AppButton.tsx", "AppButton"),  # symbol and file name
    ],
)
async def test_exact_symbol_and_identifier_searches_find_the_definition_first(
    real_db_session: AsyncSession, query: str, path: str, symbol: str
) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    outcome = await _search(real_db_session, repo_id, query, mode="keyword")

    assert outcome.hits, query
    top = outcome.hits[0]
    assert (top.path, top.symbol_name) == (path, symbol)
    assert top.semantic_rank is None and top.keyword_rank is not None
    # a name (an identifier, or a few words spelling one) also matches by exact lookup; a mere prefix does not
    assert ("exact" in top.matched_by) == (query != "loadEnv")


async def test_a_definition_outranks_chunks_that_only_mention_the_symbol(real_db_session: AsyncSession) -> None:
    caller = "".join(f"def caller_{i}():\n    return release_busy_lease(lease_id, busy=True)\n\n" for i in range(6))
    files = {"a_callers.py": (caller, "python"), "z_leases.py": ("def release_busy_lease(lease_id, busy):\n    return busy\n", "python")}
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files, chunk_max_bytes=200)

    outcome = await _search(real_db_session, repo_id, "releaseBusyLease", mode="keyword")  # camelCase for a snake_case def

    top = outcome.hits[0]
    assert (top.path, top.symbol_name, top.exact_rank) == ("z_leases.py", "release_busy_lease", 1)
    assert top.matched_by == ["keyword", "exact"] and all("exact" not in h.matched_by for h in outcome.hits[1:])


async def test_a_few_words_that_spell_a_name_find_it_by_exact_lookup(real_db_session: AsyncSession) -> None:
    mention = "".join(f"# forgot password screen note {i}\nroutes = ['forgot', 'password', 'screen']\n" for i in range(6))
    files = {
        "a_layout.py": (mention, "python"),
        "z_screen.tsx": ("export function ForgotPasswordScreen() { return null; }\n", "typescript"),
    }
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files, chunk_max_bytes=200)

    outcome = await _search(real_db_session, repo_id, "forgot password screen", mode="keyword")

    assert outcome.hits[0].symbol_name == "ForgotPasswordScreen" and outcome.hits[0].exact_rank == 1


async def test_a_file_with_the_searched_name_outranks_files_that_mention_its_words(real_db_session: AsyncSession) -> None:
    sql = "".join(f"-- push tokens table {i}\ncreate table device_push_tokens_{i} (token text, push boolean);\n" for i in range(8))
    files = {
        "migrations/device_push_tokens.sql": (sql, "sql"),
        "src/services/pushTokens.ts": ("export async function saveToken(t: string) { return t; }\n", "typescript"),
    }
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files, chunk_max_bytes=200)

    outcome = await _search(real_db_session, repo_id, "pushTokens", mode="keyword")

    assert outcome.hits[0].path == "src/services/pushTokens.ts" and outcome.hits[0].exact_rank == 1


async def test_chunks_containing_every_term_rank_before_partial_matches(real_db_session: AsyncSession) -> None:
    files = {
        "a_partial.py": ("def from_row(row):\n    return row\n", "python"),  # a heavily weighted `row` only
        "z_full.sql": ("-- enable row level security\ncreate policy p on t using (true);  -- security policy\n", "sql"),
    }
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files)

    outcome = await _search(real_db_session, repo_id, "row level security policy", mode="keyword")

    assert [h.path for h in outcome.hits][:2] == ["z_full.sql", "a_partial.py"]  # all four terms first, partial after


async def test_paths_and_content_identifiers_are_searchable(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    by_path = await _search(real_db_session, repo_id, "components ui", mode="keyword")
    by_content = await _search(real_db_session, repo_id, "OPENAI_API_KEY", mode="keyword")
    by_member = await _search(real_db_session, repo_id, "readFileSync", mode="keyword")

    assert by_path.hits[0].path == "src/components/ui/AppButton.tsx"
    assert by_content.hits[0].path == "scripts/env.ts" and "OPENAI_API_KEY" in by_content.hits[0].content
    assert by_member.hits[0].path == "scripts/env.ts"  # `fs.readFileSync(` is found by the method name


async def test_a_symbol_match_outranks_a_mere_mention_in_code(real_db_session: AsyncSession) -> None:
    files = {
        "a_mention.py": ("# notes\nresult = hash_password('x', 'y')  # calls hash_password somewhere\n", "python"),
        "z_definition.py": ("def hash_password(password, salt):\n    return password + salt\n", "python"),
    }
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files)

    outcome = await _search(real_db_session, repo_id, "hash_password", mode="keyword")

    assert outcome.hits[0].path == "z_definition.py"  # symbol and path weight beat a body mention


async def test_natural_language_keyword_search_ignores_filler_words(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)

    outcome = await _search(real_db_session, repo_id, "where do we verify a password", mode="keyword")

    assert outcome.hits[0].symbol_name == "verify_password"


async def test_hostile_and_odd_queries_are_safe(real_db_session: AsyncSession) -> None:
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id)
    before = (await real_db_session.execute(select(func.count()).select_from(CodeChunk))).scalar_one()

    for query in ["'); DROP TABLE code_chunks; --", "\\' OR 1=1", "&|!():* <->", "日本語 ünïcode", "x" * 500, "a", "!!!"]:
        outcome = await _search(real_db_session, repo_id, query, mode="keyword")
        assert isinstance(outcome.hits, list)

    assert (await _search(real_db_session, repo_id, "!!!", mode="keyword")).warnings == ["The query has no searchable terms"]
    assert (await real_db_session.execute(select(func.count()).select_from(CodeChunk))).scalar_one() == before


async def test_results_are_bounded_by_the_limit(real_db_session: AsyncSession) -> None:
    files = {f"m{i}.py": (f"def handler_{i}(request):\n    return request\n", "python") for i in range(30)}
    repo_id = await new_repository(real_db_session)
    await ingest(real_db_session, repo_id, files)

    assert len((await _search(real_db_session, repo_id, "handler request", mode="keyword", limit=5)).hits) == 5
    assert len((await _search(real_db_session, repo_id, "handler request", mode="keyword", limit=500)).hits) <= get_settings().search_max_limit


# --- citations -----------------------------------------------------------------------------------------


async def test_every_hit_cites_its_source_exactly(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    outcome = await _search(db, repo_id, "password session env load button", mode="keyword", limit=50)

    assert len(outcome.hits) >= 4
    for hit in outcome.hits:
        source = await _file_content(db, hit.source_file_id)
        data = source.encode("utf-8")
        assert data[hit.start_byte : hit.end_byte].decode("utf-8") == hit.content  # exact byte offsets
        assert hit.start_line == data[: hit.start_byte].count(b"\n") + 1  # 1-based line of the first byte
        assert hit.end_line == data[: hit.end_byte - 1].count(b"\n") + 1  # inclusive line of the last byte
        assert hit.path in CORPUS and CORPUS[hit.path][0] == source
        assert (hit.repository_id, hit.commit_sha) == (repo_id, SHA_A)


# --- semantic and hybrid ----------------------------------------------------------------------------------


async def test_semantic_search_answers_natural_language_questions(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    stats = await _embed(engine, db, repo_id, embedder)

    outcome = await _search(db, repo_id, "check a password against its stored hash", mode="semantic", embedder=embedder)

    assert stats.embedded > 3 and outcome.mode == "semantic"
    assert outcome.hits[0].symbol_name == "verify_password"
    assert outcome.hits[0].matched_by == ["semantic"] and outcome.hits[0].keyword_rank is None
    assert [h.score for h in outcome.hits] == sorted((h.score for h in outcome.hits), reverse=True)


async def test_hybrid_ranks_results_found_by_both_retrievers_first(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)

    outcome = await _search(db, repo_id, "verify password stored hash", embedder=embedder)

    assert outcome.mode == "hybrid" and outcome.warnings == []
    top = outcome.hits[0]
    assert top.symbol_name == "verify_password" and top.matched_by == ["keyword", "semantic"]
    assert top.score > outcome.hits[-1].score
    # a hit found by both is scored as the sum of its two reciprocal ranks (k = 60)
    assert top.score == pytest.approx(1 / (60 + top.keyword_rank) + 1 / (60 + top.semantic_rank))


async def test_semantic_hits_can_be_filtered_by_distance(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)

    query = "check a password against its stored hash"

    unfiltered = await _search(db, repo_id, query, mode="semantic", embedder=embedder)
    nothing_close_enough = await _search(db, repo_id, query, mode="semantic", embedder=embedder,
                                         settings=Settings(search_max_semantic_distance=0.0))
    everything_allowed = await _search(db, repo_id, query, mode="semantic", embedder=embedder,
                                       settings=Settings(search_max_semantic_distance=2.0))

    assert unfiltered.hits and nothing_close_enough.hits == []  # no vector is at distance 0 from the query
    assert [h.chunk_id for h in everything_allowed.hits] == [h.chunk_id for h in unfiltered.hits]  # None == no cutoff


# --- isolation ----------------------------------------------------------------------------------------------


async def test_repositories_are_isolated_even_with_identical_code(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    first, second = await new_repository(db, "octo/one"), await new_repository(db, "octo/two")
    await ingest(db, first)
    await ingest(db, second)
    await _embed(engine, db, first, embedder)
    shared_texts = embedder.texts_embedded
    await _embed(engine, db, second, embedder)  # identical content: embeddings are reused, not paid for again
    assert embedder.texts_embedded == shared_texts

    for repo_id in (first, second):
        for mode in ("keyword", "semantic", "hybrid"):
            outcome = await _search(db, repo_id, "verify password hash", mode=mode, embedder=embedder, limit=50)
            files = {h.source_file_id for h in outcome.hits}
            owned = set((await db.execute(select(SourceFile.id).where(SourceFile.repository_id == repo_id))).scalars())
            assert outcome.hits and files <= owned and all(h.repository_id == repo_id for h in outcome.hits)


async def test_only_the_current_commit_is_searchable(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)
    # a leftover file from an older commit that matches a rare word, with a keyword vector and an embedding
    old = SourceFile(repository_id=repo_id, commit_sha=SHA_OLD, path="old/zebra.py", language="python", category="source",
                     content="zebra = 1\n", content_hash="d" * 64, size_bytes=10, line_count=1)
    db.add(old)
    await db.flush()
    db.add(CodeChunk(source_file_id=old.id, chunk_index=0, language="python", kind="code", parser_mode="ast", start_line=1,
                     end_line=1, start_byte=0, end_byte=10, content="zebra = 1\n", content_hash="e" * 64))
    await db.flush()
    await db.execute(update(CodeChunk).where(CodeChunk.source_file_id == old.id).values(search_vector=func.to_tsvector("simple", "zebra")))
    db.add(CodeEmbedding(content_hash="e" * 64, model=embedder.key, embedding=__import__("tests.search_support", fromlist=["x"]).fake_vector("zebra")))
    await db.commit()

    for mode in ("keyword", "semantic", "hybrid"):
        outcome = await _search(db, repo_id, "zebra", mode=mode, embedder=embedder, limit=50)
        assert all(h.path != "old/zebra.py" and h.commit_sha == SHA_A for h in outcome.hits)

    # moving the repository to a commit that has no chunks yet hides the old snapshot entirely
    await db.execute(update(Repository).where(Repository.id == repo_id).values(last_ingested_commit_sha=SHA_B))
    await db.commit()
    with pytest.raises(NotIndexedError):
        await _search(db, repo_id, "verify password", mode="keyword")


# --- fallbacks and failures ------------------------------------------------------------------------------------


async def test_keyword_search_works_without_any_embedding_provider(real_db_session: AsyncSession) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    outcome = await _search(db, repo_id, "verify_password", embedder=None)  # hybrid requested, nothing configured

    assert outcome.mode == "keyword" and outcome.hits[0].symbol_name == "verify_password"
    assert outcome.index.semantic == "disabled"
    assert outcome.warnings == ["Semantic search is not configured (set GEMINI_API_KEY); showing keyword results only"]
    with pytest.raises(SemanticUnavailableError, match="not configured") as caught:
        await _search(db, repo_id, "verify password", mode="semantic", embedder=None)
    assert not caught.value.temporary


async def test_an_unbuilt_semantic_index_falls_back_to_keyword(real_db_session: AsyncSession) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    outcome = await _search(db, repo_id, "verify password", embedder=embedder)

    assert outcome.mode == "keyword" and outcome.index.semantic == "not_built" and embedder.query_calls == 0
    assert "has not been built" in outcome.warnings[0]
    with pytest.raises(SemanticUnavailableError, match="not been built"):
        await _search(db, repo_id, "verify password", mode="semantic", embedder=embedder)


async def test_a_provider_failure_at_search_time_falls_back_to_keyword(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, FakeEmbedder())
    broken = FakeEmbedder(fail_queries=True)

    outcome = await _search(db, repo_id, "verify password", embedder=broken)

    assert outcome.mode == "keyword" and outcome.hits[0].symbol_name == "verify_password"
    assert outcome.warnings == ["Semantic search is temporarily unavailable; showing keyword results only"]
    with pytest.raises(SemanticUnavailableError) as caught:
        await _search(db, repo_id, "verify password", mode="semantic", embedder=broken)
    assert caught.value.temporary


async def test_a_partial_index_is_reported_and_still_searchable(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    stats = await _embed(engine, db, repo_id, embedder, embedding_max_chunks_per_repository=2)

    outcome = await _search(db, repo_id, "password", embedder=embedder)

    assert (stats.embedded, stats.skipped) == (2, stats.skipped) and stats.skipped > 0
    assert outcome.index.semantic == "partial" and 0 < outcome.index.embedded_chunks < outcome.index.chunks
    assert any("still being built" in w for w in outcome.warnings) and outcome.hits


async def test_missing_and_unindexed_repositories(real_db_session: AsyncSession) -> None:
    db = real_db_session
    with pytest.raises(RepositoryNotFoundError):
        await _search(db, 999_999_999, "anything")
    fresh = Repository(github_repo_id=99_999_999, full_name="x/y", default_branch="main")
    db.add(fresh)
    await db.commit()
    with pytest.raises(NotIndexedError, match="not been ingested"):
        await _search(db, fresh.id, "anything")


# --- the embedding index: bounds, resume, reuse, locking -------------------------------------------------------------


async def test_embedding_work_is_batched_deduplicated_and_bounded(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    files = {f"f{i}.py": ("def same():\n    return 1\n", "python") for i in range(10)}  # ten identical files
    files |= {f"g{i}.py": (f"def unique_{i}():\n    return {i}\n", "python") for i in range(7)}
    await ingest(db, repo_id, files)

    stats = await _embed(engine, db, repo_id, embedder, embedding_batch_size=4)

    assert stats.embedded == 8 and embedder.texts_embedded == 8  # 7 unique + 1 shared text, not 17
    assert [len(b) for b in embedder.batches] == [4, 4] and stats.batches == 2
    assert (await db.execute(select(func.count()).select_from(CodeEmbedding))).scalar_one() == 8
    again = await _embed(engine, db, repo_id, embedder, embedding_batch_size=4)
    assert (again.embedded, again.already_embedded, embedder.texts_embedded) == (0, 8, 8)  # idempotent


async def test_a_failure_keeps_finished_batches_and_a_retry_resumes(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    files = {f"g{i}.py": (f"def unique_{i}():\n    return {i}\n", "python") for i in range(9)}
    await ingest(db, repo_id, files)
    flaky = FakeEmbedder(fail_on_call=2)  # the second batch fails

    with pytest.raises(EmbeddingProviderError) as caught:
        await _embed(engine, db, repo_id, flaky, embedding_batch_size=4)

    assert caught.value.retryable
    assert (await db.execute(select(func.count()).select_from(CodeEmbedding))).scalar_one() == 4  # batch 1 survived
    healthy = FakeEmbedder()
    resumed = await _embed(engine, db, repo_id, healthy, embedding_batch_size=4)
    assert (resumed.embedded, resumed.already_embedded) == (5, 4) and healthy.texts_embedded == 5  # only the rest


async def test_the_job_retries_transient_failures_and_drops_permanent_ones(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    with pytest.raises(TransientEmbeddingError):
        await run_embedding(engine, _sessions(db), repo_id, FakeEmbedder(fail_on_call=1, fail_retryable=True))
    await run_embedding(engine, _sessions(db), repo_id, FakeEmbedder(fail_on_call=1, fail_retryable=False))  # logged, no raise
    assert (await index_status(db, repo_id, embeddings_enabled=True)).embedded_chunks == 0  # search still keyword-only
    assert (await _search(db, repo_id, "verify_password", mode="keyword")).hits  # source index unaffected


async def test_two_embedding_jobs_never_run_for_one_repository(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    async with embedding_lock(engine, repo_id) as held:
        assert held and (await index_status(db, repo_id, embeddings_enabled=True)).embedding_job_running is True
        blocked = await _embed(engine, db, repo_id, embedder)
        assert blocked.already_running and embedder.texts_embedded == 0
    assert (await index_status(db, repo_id, embeddings_enabled=True)).embedding_job_running is False  # released
    assert (await _embed(engine, db, repo_id, embedder)).embedded > 0


async def test_reingestion_reuses_embeddings_and_replaces_the_index(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)
    first_count = embedder.texts_embedded

    await ingest(db, repo_id)  # same commit again: chunks are rewritten...
    again = await _embed(engine, db, repo_id, embedder)
    assert again.embedded == 0 and embedder.texts_embedded == first_count  # ...and their vectors reused for free
    assert (await index_status(db, repo_id, embeddings_enabled=True)).semantic == "ready"

    changed = {**CORPUS, "backend/auth.py": (CORPUS["backend/auth.py"][0].replace("sha256", "blake2b"), "python")}
    await ingest(db, repo_id, changed, sha=SHA_B)  # a new commit that changes one file
    status = await index_status(db, repo_id, embeddings_enabled=True)
    assert status.commit_sha == SHA_B and status.semantic == "partial"  # new chunks have no vector yet
    update = await _embed(engine, db, repo_id, embedder)
    assert 0 < update.embedded < first_count  # only the changed chunks are embedded
    assert (await index_status(db, repo_id, embeddings_enabled=True)).semantic == "ready"

    fresh = await _search(db, repo_id, "blake2b", mode="keyword")
    stale = await _search(db, repo_id, "sha256", mode="keyword")
    assert fresh.hits and fresh.hits[0].commit_sha == SHA_B and not stale.hits  # old text is gone from the index


async def test_embeddings_of_different_models_never_mix(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)
    await db.execute(update(CodeEmbedding).values(model="some-other-model/1536"))  # vectors from another model
    await db.commit()

    status = await index_status(db, repo_id, embeddings_enabled=True)

    assert status.embedded_chunks == 0 and status.semantic == "not_built"  # they are invisible to this model
    outcome = await _search(db, repo_id, "verify password", embedder=embedder)
    assert outcome.mode == "keyword"


async def test_index_status_separates_source_ingestion_from_embedding_readiness(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)

    before = await index_status(db, repo_id, embeddings_enabled=True)
    disabled = await index_status(db, repo_id, embeddings_enabled=False)
    await _embed(engine, db, repo_id, embedder)
    after = await index_status(db, repo_id, embeddings_enabled=True)

    assert before.chunks > 0 and before.embedded_chunks == 0 and before.semantic == "not_built"  # ingested, not embedded
    assert disabled.semantic == "disabled" and disabled.chunks == before.chunks
    assert after.semantic == "ready" and after.embedded_chunks == after.chunks
    assert math.isclose(after.chunks, before.chunks)


async def test_the_migration_backfill_builds_the_same_vectors_as_ingestion(real_db_session: AsyncSession) -> None:
    """Chunks stored before the search column existed are backfilled in SQL; it must match the Python path."""
    from apps.api.services.code_search import SEARCH_VECTOR_BACKFILL_SQL

    db = real_db_session
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    written = dict((await db.execute(text("SELECT id, search_vector::text FROM code_chunks"))).all())
    assert written and all(written.values())

    await db.execute(update(CodeChunk).values(search_vector=None))  # pretend they predate the column
    await db.execute(text(SEARCH_VECTOR_BACKFILL_SQL))
    rebuilt = dict((await db.execute(text("SELECT id, search_vector::text FROM code_chunks"))).all())

    assert rebuilt == written  # identical lexemes, positions and weights for every chunk


# --- Gemini specifics -----------------------------------------------------------------------------------------


async def test_documents_and_queries_are_embedded_with_the_paired_task_types(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)

    await _search(db, repo_id, "how are passwords verified", embedder=embedder)

    assert set(embedder.tasks) == {"RETRIEVAL_DOCUMENT"}  # every chunk text, however many batches
    assert embedder.query_tasks == ["CODE_RETRIEVAL_QUERY"]  # the one query


async def test_batches_never_exceed_the_provider_limit(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    files = {f"g{i}.py": (f"def unique_{i}():\n    return {i}\n", "python") for i in range(130)}
    await ingest(db, repo_id, files)

    stats = await _embed(engine, db, repo_id, embedder, embedding_batch_size=500)  # asking for more than the API allows

    assert max(len(b) for b in embedder.batches) == 100 and stats.embedded == embedder.texts_embedded == 130
    assert [len(b) for b in embedder.batches] == [100, 30]


async def test_every_stored_vector_has_exactly_1536_dimensions(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)

    dims = (await db.execute(text("SELECT DISTINCT vector_dims(embedding) FROM code_embeddings"))).scalars().all()
    models = (await db.execute(select(CodeEmbedding.model).distinct())).scalars().all()

    assert dims == [1536] and models == ["gemini-embedding-001/1536"]


async def test_vectors_stored_under_another_providers_model_key_are_ignored(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db, embedder = real_db_session, FakeEmbedder()
    repo_id = await new_repository(db)
    await ingest(db, repo_id)
    await _embed(engine, db, repo_id, embedder)
    await db.execute(update(CodeEmbedding).values(model="text-embedding-3-small/1536"))  # e.g. left over from OpenAI
    await db.commit()

    assert (await index_status(db, repo_id, embeddings_enabled=True)).embedded_chunks == 0  # not reused, not mixed
    rebuilt = await _embed(engine, db, repo_id, FakeEmbedder())
    assert rebuilt.embedded > 0 and rebuilt.already_embedded == 0  # the current model's vectors are built fresh
    assert (await index_status(db, repo_id, embeddings_enabled=True)).semantic == "ready"


# --- rate limits: the incident that stalled ClassLens at partial readiness ----------------------------------------------


def _gemini(handler, **settings: object):  # type: ignore[no-untyped-def]
    import httpx

    from apps.api.services.embeddings import GeminiEmbedder

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    embedder = GeminiEmbedder(Settings(gemini_api_key="test-key-not-real", _env_file=None, **settings), client=client, sleep=fake_sleep)  # type: ignore[arg-type]
    embedder.sleeps = sleeps  # type: ignore[attr-defined]
    return embedder


def _quota_server(*, limit_every: int, quota: str = "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier", delay: str = "20s"):  # type: ignore[no-untyped-def]
    """A fake Gemini that answers 429 to every request except each `limit_every`-th (a per-minute quota)."""
    import json

    import httpx

    from tests.search_support import fake_vector

    state = {"calls": 0, "served": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] % limit_every != 0:
            body = {"error": {"status": "RESOURCE_EXHAUSTED", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay},
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota}]}]}}
            return httpx.Response(429, json=body)
        texts = [r["content"]["parts"][0]["text"] for r in json.loads(request.content)["requests"]]
        state["served"] += len(texts)
        return httpx.Response(200, json={"embeddings": [{"values": fake_vector(t)} for t in texts]})

    return handler, state


async def test_an_embedding_job_rides_out_per_minute_rate_limits_and_finishes(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    db = real_db_session
    repo_id = await new_repository(db)
    files = {f"g{i}.py": (f"def unique_{i}():\n    return {i}\n", "python") for i in range(150)}
    await ingest(db, repo_id, files)
    handler, state = _quota_server(limit_every=3)  # two 429s, then one success, repeatedly
    embedder = _gemini(handler, embedding_batch_size=32)

    stats = await _embed(engine, db, repo_id, embedder, embedding_batch_size=32)  # type: ignore[arg-type]

    assert stats.embedded == state["served"] and stats.skipped == 0 and stats.batches == 5
    assert (await index_status(db, repo_id, embeddings_enabled=True)).semantic == "ready"
    assert state["calls"] == 15 and len(embedder.sleeps) == 10  # type: ignore[attr-defined]  # waited 20 s on each 429, no failure
    assert set(embedder.sleeps) == {20.25}  # type: ignore[attr-defined]  # the delay Google asked for, never 1 s


async def test_progress_survives_a_daily_quota_and_a_later_run_resumes_without_repeating_work(real_db_session: AsyncSession, engine: AsyncEngine) -> None:
    from apps.api.services.embeddings import EmbeddingProviderError

    db = real_db_session
    repo_id = await new_repository(db)
    files = {f"g{i}.py": (f"def unique_{i}():\n    return {i}\n", "python") for i in range(100)}
    await ingest(db, repo_id, files)
    ok, state = _quota_server(limit_every=1)  # every call succeeds
    batches = {"n": 0}

    def exhausted_after_two_batches(request):  # type: ignore[no-untyped-def]
        import httpx

        batches["n"] += 1
        if batches["n"] <= 2:
            return ok(request)
        return httpx.Response(429, json={"error": {"details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                                                  "violations": [{"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}]}]}})

    with pytest.raises(EmbeddingProviderError) as caught:
        await _embed(engine, db, repo_id, _gemini(exhausted_after_two_batches), embedding_batch_size=32)

    assert not caught.value.retryable and "daily quota" in str(caught.value)
    assert (await index_status(db, repo_id, embeddings_enabled=True)).embedded_chunks == 64  # two batches were kept

    resumed_handler, resumed_state = _quota_server(limit_every=1)
    stats = await _embed(engine, db, repo_id, _gemini(resumed_handler), embedding_batch_size=32)  # the next day

    assert (stats.embedded, stats.already_embedded) == (36, 64) and resumed_state["served"] == 36  # only the rest
    assert (await index_status(db, repo_id, embeddings_enabled=True)).semantic == "ready"
