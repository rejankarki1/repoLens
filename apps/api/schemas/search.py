"""Search API response schemas."""

from pydantic import BaseModel


class IndexStatusResponse(BaseModel):
    repository_id: int
    commit_sha: str | None
    chunks: int  # code chunks in the current snapshot (the keyword index covers all of them)
    embedded_chunks: int  # chunks that also have a vector for the configured model
    semantic: str  # disabled | not_built | partial | ready
    model: str
    embedding_job_running: bool


class SearchHitResponse(BaseModel):
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
    start_line: int  # 1-based, inclusive
    end_line: int
    start_byte: int  # UTF-8 offsets into the file's stored content; exact even for partial-line chunks
    end_byte: int
    content: str
    score: float  # reciprocal-rank-fusion score; hits are ordered best first
    matched_by: list[str]  # keyword, exact (symbol or file-name lookup) and/or semantic
    keyword_rank: int | None
    semantic_rank: int | None
    exact_rank: int | None


class SearchResponse(BaseModel):
    query: str
    mode: str  # what actually ran (hybrid falls back to keyword when semantic search is unavailable)
    hits: list[SearchHitResponse]
    warnings: list[str]
    index: IndexStatusResponse
    took_ms: float
