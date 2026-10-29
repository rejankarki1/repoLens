"""Repository-scoped hybrid code search: PostgreSQL full-text search + pgvector, fused with RRF.

*Scope.* Every query is limited to one repository's **current** snapshot: chunks whose file belongs to
the repository and has the repository's `last_ingested_commit_sha`. Rows from older commits or other
repositories can never be returned, whatever they contain.

*Keyword* (`code_chunks.search_vector`, GIN-indexed). The document is the normalized path and symbol
names (weight A) plus the normalized code (weight C). Normalization strips punctuation (PostgreSQL
would otherwise keep `foo.bar` or `src/a/B.tsx` as one token) and adds camelCase pieces, so `loadEnv`,
`load env` and `load_env_local` all find `loadEnvLocal`. The query is built from sanitized lexemes only
and passed as a bound parameter to `to_tsquery`, so user text can never inject query operators or SQL.

*Semantic* (`code_embeddings`, cosine distance). The query is embedded once (cached); chunks come from
the same scope, joined to vectors by content hash and model.

*Exact* (symbol or file name). A query that looks like a name (one identifier such as `runProcessingJob`,
`match_enrolled_course`, `pushTokens`, or up to four words such as `forgot password screen`) also looks
up chunks whose primary symbol, or whose file name, equals it ignoring case, underscores and punctuation. Definitions and same-named files therefore rank
above chunks that merely mention the word.

*Hybrid.* Reciprocal Rank Fusion: score = sum over retrievers of 1 / (k + rank), k = 60. It needs only
ranks, so keyword scores and distances never have to be calibrated against each other, and every
result says which retriever found it and at what rank. Keyword search itself ranks chunks that contain
*all* query terms before chunks that contain only some.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.config import Settings, get_settings
from apps.api.services.embeddings import EmbeddingError, GeminiEmbedder, embedding_job_running, model_key

# --- text normalization (shared by indexing and querying) ---------------------------------------

_NON_WORD = re.compile(r"[^A-Za-z0-9_]+")
_LOWER_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")
_TERM = re.compile(r"[A-Za-z0-9_]+")
_NON_ALNUM = re.compile(r"[^a-z0-9]")
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it of on or that the this to was what when where "
    "which who why with their them they we you your my our its if then than there these those so not no".split()
)
MAX_QUERY_TERMS = 12

# The same normalization in SQL, used to backfill chunks stored before the column existed.
# (Keep in sync with `_clean` / `split_camel` above; a test checks they produce identical documents.)
_SQL_SPLIT = (
    "regexp_replace(regexp_replace({x}, '([a-z0-9])([A-Z])', '\\1 \\2', 'g'), '([A-Z]+)([A-Z][a-z])', '\\1 \\2', 'g')"
)
_SQL_CLEAN = "regexp_replace({x}, '[^A-Za-z0-9_]+', ' ', 'g')"
SEARCH_VECTOR_BACKFILL_SQL = f"""
UPDATE code_chunks AS c
SET search_vector = d.vec
FROM (
    SELECT c2.id,
           setweight(to_tsvector('simple', m.meta || ' ' || {_SQL_SPLIT.format(x='m.meta')}), 'A')
           || setweight(to_tsvector('simple', b.body || ' ' || {_SQL_SPLIT.format(x='b.body')}), 'C') AS vec
    FROM code_chunks AS c2
    JOIN source_files AS sf ON sf.id = c2.source_file_id
    CROSS JOIN LATERAL (SELECT {_SQL_CLEAN.format(x="sf.path || ' ' || coalesce((SELECT string_agg(s, ' ') FROM jsonb_array_elements_text(c2.symbols) AS s), '')")} AS meta) AS m
    CROSS JOIN LATERAL (SELECT {_SQL_CLEAN.format(x='c2.content')} AS body) AS b
    WHERE c2.search_vector IS NULL
) AS d
WHERE c.id = d.id
""".strip()


def _clean(text_: str) -> str:
    return _NON_WORD.sub(" ", text_)


def split_camel(text_: str) -> str:
    """`loadEnvLocal HTTPResponse2` -> `load Env Local HTTP Response2`."""
    return _ACRONYM.sub(r"\1 \2", _LOWER_UPPER.sub(r"\1 \2", text_))


def search_documents(path: str, symbols: Sequence[str], content: str) -> tuple[str, str]:
    """(meta_text, body_text) that become the weight-A and weight-C parts of a chunk's search vector."""
    meta = _clean(f"{path} {' '.join(symbols)}")
    body = _clean(content)
    return f"{meta} {split_camel(meta)}", f"{body} {split_camel(body)}"


_NAME_LIKE = re.compile(r"[A-Za-z0-9_\-]+(?:\s+[A-Za-z0-9_\-]+){0,3}")


def exact_key(query: str) -> str | None:
    """The normalized form (lowercase letters and digits only) of a query that looks like a name, else None.

    A name is one identifier (`runProcessingJob`, `match_enrolled_course`) or up to four words that could
    be the pieces of one (`forgot password screen` -> `forgotpasswordscreen`). Longer text is a question,
    not a name, and gets no exact lookup.
    """
    stripped = query.strip()
    if not _NAME_LIKE.fullmatch(stripped):
        return None
    key = _NON_ALNUM.sub("", stripped.lower())
    return key if len(key) >= 3 else None


def build_tsquery(query: str, operator: str = "|") -> str | None:
    """A `to_tsquery('simple', ...)` string for free text or an identifier, or None if nothing is searchable.

    Built only from `[a-z0-9]` lexemes and our own operators. Plain words match as prefixes (`auth:*`);
    `camelCase` terms match as the whole token (prefix) or all of their parts; `snake_case` terms match
    all their parts. Terms are OR-ed (`operator="|"`) so a natural-language question still finds partial
    matches; `operator="&"` requires every term and is used to rank full matches first.
    """
    clauses: list[str] = []
    for term in _TERM.findall(query):
        words = [w.lower() for w in split_camel(term.replace("_", " ")).split()]
        if not words:
            continue
        if len(words) == 1:
            word = words[0]
            if len(word) < 2 or (word in _STOPWORDS and term.islower()):
                continue
            clause = f"{word}:*" if len(word) >= 3 else word
        else:
            parts = " & ".join(words)
            clause = parts if "_" in term else f"({''.join(words)}:* | ({parts}))"
        if clause not in clauses:
            clauses.append(clause)
    return f" {operator} ".join(clauses[:MAX_QUERY_TERMS]) or None


# --- ranking ------------------------------------------------------------------------------------


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[int]], k: int = 60
) -> list[tuple[int, float, dict[str, int]]]:
    """Fuse ranked id lists. Returns (id, score, {retriever: 1-based rank}), best first."""
    ranks: dict[int, dict[str, int]] = {}
    for name, ids in rankings.items():
        for position, item in enumerate(ids, start=1):
            ranks.setdefault(item, {}).setdefault(name, position)
    fused = [(item, sum(1.0 / (k + r) for r in by.values()), by) for item, by in ranks.items()]
    return sorted(fused, key=lambda f: (-f[1], min(f[2].values()), f[0]))


# --- errors and result types --------------------------------------------------------------------


class SearchError(Exception):
    """Base class for search failures that the API maps to HTTP errors."""


class RepositoryNotFoundError(SearchError):
    pass


class NotIndexedError(SearchError):
    """The repository has no ingested snapshot, or its snapshot has no chunks yet."""


class SemanticUnavailableError(SearchError):
    """Semantic search was required but is not configured, not built, or the provider failed."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary  # True for a provider outage that may clear up


@dataclass(frozen=True, slots=True)
class IndexStatus:
    repository_id: int
    commit_sha: str | None
    chunks: int
    embedded_chunks: int
    semantic: str  # disabled | not_built | partial | ready
    model: str
    embedding_job_running: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: int
    repository_id: int
    commit_sha: str
    source_file_id: int
    path: str
    language: str
    kind: str
    symbol_name: str | None
    symbols: list[str]
    parser_mode: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    content: str
    score: float
    keyword_rank: int | None
    semantic_rank: int | None
    exact_rank: int | None = None

    @property
    def matched_by(self) -> list[str]:
        ranks = (("keyword", self.keyword_rank), ("exact", self.exact_rank), ("semantic", self.semantic_rank))
        return [name for name, rank in ranks if rank is not None]


@dataclass(slots=True)
class SearchOutcome:
    query: str
    mode: str  # what actually ran: hybrid | keyword | semantic
    hits: list[SearchHit]
    index: IndexStatus
    warnings: list[str] = field(default_factory=list)
    took_ms: float = 0.0


# --- queries ------------------------------------------------------------------------------------

_COLUMNS = (
    "c.id AS chunk_id, c.source_file_id, sf.path, c.language, c.kind, c.symbol_name, c.symbols, c.parser_mode, "
    "c.start_line, c.end_line, c.start_byte, c.end_byte, c.content"
)
_ORDER_TIEBREAK = "sf.path, c.chunk_index"

_KEYWORD_SQL = f"""
WITH q AS (SELECT to_tsquery('simple', :loose) AS loose, to_tsquery('simple', :strict) AS strict)
SELECT {_COLUMNS}
FROM q, code_chunks c JOIN source_files sf ON sf.id = c.source_file_id
WHERE sf.repository_id = :repo AND sf.commit_sha = :sha AND c.search_vector @@ q.loose
ORDER BY (c.search_vector @@ q.strict) DESC, ts_rank_cd(c.search_vector, q.loose) DESC, {_ORDER_TIEBREAK}
LIMIT :limit
"""

_EXACT_SQL = rf"""
SELECT {_COLUMNS}
FROM code_chunks c JOIN source_files sf ON sf.id = c.source_file_id
WHERE sf.repository_id = :repo AND sf.commit_sha = :sha
  AND (lower(replace(c.symbol_name, '_', '')) = :key
       OR regexp_replace(lower(regexp_replace(regexp_replace(sf.path, '^.*/', ''), '\.[^.]*$', '')), '[^a-z0-9]', '', 'g') = :key)
ORDER BY (lower(replace(c.symbol_name, '_', '')) = :key) DESC, sf.path, c.chunk_index
LIMIT :limit
"""

_SEMANTIC_SQL = f"""
SELECT {_COLUMNS}, e.embedding <=> CAST(:qvec AS vector) AS distance
FROM code_chunks c
JOIN source_files sf ON sf.id = c.source_file_id
JOIN code_embeddings e ON e.content_hash = c.content_hash AND e.model = :model
WHERE sf.repository_id = :repo AND sf.commit_sha = :sha
ORDER BY distance, {_ORDER_TIEBREAK}
LIMIT :limit
"""

_STATUS_SQL = """
SELECT count(*) AS chunks, count(e.content_hash) AS embedded
FROM code_chunks c
JOIN source_files sf ON sf.id = c.source_file_id
LEFT JOIN code_embeddings e ON e.content_hash = c.content_hash AND e.model = :model
WHERE sf.repository_id = :repo AND sf.commit_sha = :sha
"""


async def index_status(
    db: AsyncSession, repository_id: int, *, embeddings_enabled: bool, settings: Settings | None = None
) -> IndexStatus:
    """Source-index and embedding-index readiness for a repository's current snapshot."""
    settings = settings or get_settings()
    model = model_key(settings)
    commit = (
        await db.execute(text("SELECT last_ingested_commit_sha FROM repositories WHERE id = :id"), {"id": repository_id})
    ).first()
    if commit is None:
        raise RepositoryNotFoundError(f"Repository {repository_id} not found")
    sha = commit[0]
    chunks = embedded = 0
    if sha is not None:
        row = (await db.execute(text(_STATUS_SQL), {"repo": repository_id, "sha": sha, "model": model})).one()
        chunks, embedded = row.chunks, row.embedded
    if not embeddings_enabled:
        semantic = "disabled"
    elif chunks == 0 or embedded == 0:
        semantic = "not_built"
    else:
        semantic = "ready" if embedded >= chunks else "partial"
    return IndexStatus(
        repository_id=repository_id,
        commit_sha=sha,
        chunks=chunks,
        embedded_chunks=embedded,
        semantic=semantic,
        model=model,
        embedding_job_running=await embedding_job_running(db, repository_id),
    )


def _hit(row: Any, repository_id: int, sha: str) -> dict[str, Any]:
    return {
        "chunk_id": row.chunk_id, "repository_id": repository_id, "commit_sha": sha,
        "source_file_id": row.source_file_id, "path": row.path, "language": row.language, "kind": row.kind,
        "symbol_name": row.symbol_name, "symbols": list(row.symbols or []), "parser_mode": row.parser_mode,
        "start_line": row.start_line, "end_line": row.end_line, "start_byte": row.start_byte,
        "end_byte": row.end_byte, "content": row.content,
    }


async def search_repository(
    db: AsyncSession,
    repository_id: int,
    query: str,
    *,
    limit: int = 10,
    mode: str = "hybrid",
    embedder: GeminiEmbedder | None = None,
    settings: Settings | None = None,
) -> SearchOutcome:
    """Search a repository's current snapshot. See the module docstring for how results are found."""
    settings = settings or get_settings()
    started = time.perf_counter()
    status = await index_status(db, repository_id, embeddings_enabled=embedder is not None, settings=settings)
    if status.commit_sha is None:
        raise NotIndexedError("This repository has not been ingested yet")
    if status.chunks == 0:
        raise NotIndexedError("This repository's snapshot has no code chunks yet; re-analyze it to build the index")

    limit = max(1, min(limit, settings.search_max_limit))
    scope = {"repo": repository_id, "sha": status.commit_sha}
    outcome = SearchOutcome(query=query, mode=mode, hits=[], index=status)
    rows: dict[int, dict[str, Any]] = {}
    rankings: dict[str, list[int]] = {}

    if mode in ("hybrid", "keyword"):
        loose = build_tsquery(query)
        if loose is None:
            outcome.warnings.append("The query has no searchable terms")
        else:
            found = (
                await db.execute(
                    text(_KEYWORD_SQL),
                    {**scope, "loose": loose, "strict": build_tsquery(query, "&"), "limit": settings.search_candidates},
                )
            ).all()
            rankings["keyword"] = [r.chunk_id for r in found]
            rows.update({r.chunk_id: _hit(r, repository_id, status.commit_sha) for r in found})
        key = exact_key(query)
        if key is not None:
            found = (await db.execute(text(_EXACT_SQL), {**scope, "key": key, "limit": settings.search_candidates})).all()
            if found:
                rankings["exact"] = [r.chunk_id for r in found]
                for r in found:
                    rows.setdefault(r.chunk_id, _hit(r, repository_id, status.commit_sha))

    if mode in ("hybrid", "semantic"):
        reason, temporary = None, False
        if embedder is None:
            reason = "Semantic search is not configured (set GEMINI_API_KEY)"
        elif status.embedded_chunks == 0:
            reason = "The semantic index has not been built for this repository yet"
        else:
            try:
                vector = await embedder.embed_query(query)
                found = (
                    await db.execute(
                        text(_SEMANTIC_SQL),
                        {**scope, "qvec": "[" + ",".join(repr(float(x)) for x in vector) + "]", "model": status.model,
                         "limit": settings.search_candidates},
                    )
                ).all()
                cutoff = settings.search_max_semantic_distance
                found = [r for r in found if cutoff is None or r.distance <= cutoff]
                rankings["semantic"] = [r.chunk_id for r in found]
                for r in found:
                    rows.setdefault(r.chunk_id, _hit(r, repository_id, status.commit_sha))
            except EmbeddingError:
                reason, temporary = "Semantic search is temporarily unavailable", True
        if reason is not None:
            if mode == "semantic":
                raise SemanticUnavailableError(reason, temporary=temporary)
            outcome.warnings.append(f"{reason}; showing keyword results only")

    lexical = "keyword" in rankings or "exact" in rankings
    outcome.mode = "hybrid" if lexical and "semantic" in rankings else ("semantic" if "semantic" in rankings else "keyword")
    if status.semantic == "partial" and "semantic" in rankings:
        outcome.warnings.append(
            f"The semantic index is still being built ({status.embedded_chunks} of {status.chunks} chunks)"
        )
    for chunk_id, score, ranks in reciprocal_rank_fusion(rankings, settings.search_rrf_k)[:limit]:
        outcome.hits.append(
            SearchHit(
                **rows[chunk_id], score=score, keyword_rank=ranks.get("keyword"),
                semantic_rank=ranks.get("semantic"), exact_rank=ranks.get("exact"),
            )
        )
    outcome.took_ms = (time.perf_counter() - started) * 1000
    return outcome
