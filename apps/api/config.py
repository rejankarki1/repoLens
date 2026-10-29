"""Environment-based configuration for the API."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = (
        "postgresql+asyncpg://repolens:local_dev_password@127.0.0.1:5433/repolens"
    )
    redis_url: str = "redis://127.0.0.1:6379/0"
    web_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    github_request_timeout_seconds: float = 10.0
    github_archive_max_bytes: int = 25 * 1024 * 1024
    github_archive_read_timeout_seconds: float = 60.0
    archive_reader_max_file_bytes: int = 1_048_576
    archive_reader_max_files_accepted: int = 5_000
    archive_reader_max_total_bytes: int = 52_428_800
    archive_reader_max_entries_scanned: int = 20_000
    archive_reader_max_path_length: int = 1_024
    # Maximum size of one code chunk in UTF-8 bytes (minimum 16, see services/code_parser.py).
    code_chunk_max_bytes: int = 1_500
    ingestion_queue_name: str = "ingestion"
    ingestion_job_timeout_seconds: int = 900
    # One retry per entry, for transient failures only (see jobs/ingestion_job.py).
    ingestion_retry_intervals_seconds: list[int] = [30, 120]
    ingestion_failure_ttl_seconds: int = 86_400
    # A running job refreshes `ingestion_heartbeat_at` this often.
    ingestion_heartbeat_interval_seconds: float = 15.0
    # A job is stale when its heartbeat is older than these (and RQ no longer knows a live job for it).
    ingestion_stale_running_seconds: int = 90
    ingestion_stale_queued_seconds: int = 900
    # How long Redis keeps a worker registered after its last heartbeat. A crashed worker's running
    # job only becomes recoverable once this expires (RQ's default would be 7 minutes).
    ingestion_worker_ttl_seconds: int = 60
    # How often the API process looks for stale jobs; 0 disables the periodic sweep.
    ingestion_sweep_interval_seconds: float = 60.0
    # Temp archives older than this cannot belong to a live job (jobs are killed at the job timeout).
    ingestion_orphan_archive_max_age_seconds: int = 1_800

    # --- Semantic search (see docs/search.md). Embeddings are optional: without a key, search is keyword-only.
    gemini_api_key: SecretStr | None = None  # env GEMINI_API_KEY; never logged or returned by the API
    embedding_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 1_536  # must equal the width of code_embeddings.embedding (a schema constant)
    embedding_batch_size: int = 64
    # Upper bound on distinct chunk texts embedded per job run (about 350 tokens each, so 10,000 is ~3.5M tokens).
    embedding_max_chunks_per_repository: int = 10_000
    embedding_request_timeout_seconds: float = 30.0
    embedding_query_timeout_seconds: float = 5.0  # budget for the one embedding call a search makes
    # Rate limits (HTTP 429): wait for the delay Google asks for (capped), up to this many tries per batch. A
    # longer wait than the cap is left to the RQ retry schedule below instead of blocking the job.
    embedding_rate_limit_attempts: int = 6
    embedding_max_rate_limit_wait_seconds: float = 60.0
    # RQ retries for an embedding job that still fails. Quotas reset per minute (or per day), so this is much
    # longer than the ingestion schedule: finished batches are kept and every retry resumes where it stopped.
    embedding_retry_intervals_seconds: list[int] = [60, 180, 300, 600]
    search_candidates: int = 50  # per retriever, before fusion
    search_max_limit: int = 50
    search_rrf_k: int = 60
    # Cosine distance above which a vector hit is ignored; None keeps every nearest neighbour. Calibrate
    # against real embeddings before setting it.
    search_max_semantic_distance: float | None = None

    # --- Answer generation / RAG (see docs/rag.md). Uses the same GEMINI_API_KEY as embeddings.
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_generation_model: str = "gemini-3.5-flash"
    gemini_generation_timeout_seconds: float = 60.0  # per read: how long Gemini may stay silent
    gemini_generation_deadline_seconds: float = 120.0  # whole answer, including streaming
    gemini_generation_temperature: float = 0.2
    # Includes thinking tokens on Gemini 2.5 models, so thinking is switched off (budget 0) by default: the
    # answer is grounded in the supplied excerpts, and it keeps latency and cost low. None omits the setting.
    gemini_generation_max_output_tokens: int = 2_048
    gemini_thinking_budget: int | None = 0
    rag_context_chunks: int = 8  # most source excerpts sent with a question
    rag_context_max_chars: int = 24_000  # and the most code characters across them
    rag_history_messages: int = 6  # most recent conversation messages sent as follow-up context
    rag_history_max_chars: int = 2_000  # per history message
    rag_question_max_chars: int = 2_000

    # Which provider writes answers. Embeddings always use Gemini, whatever this is set to. No automatic fallback.
    generation_provider: Literal["gemini", "groq"] = "gemini"  # env GENERATION_PROVIDER
    groq_api_key: SecretStr | None = None  # env GROQ_API_KEY; never logged or returned by the API
    groq_api_base_url: str = "https://api.groq.com/openai/v1"
    groq_generation_model: str = "llama-3.3-70b-versatile"
    # Only for reasoning models such as openai/gpt-oss-120b (low | medium | high); unset sends nothing.
    groq_reasoning_effort: str | None = None
    # Groq also uses the gemini_generation_timeout/deadline/temperature/max_output_tokens settings above.

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def embeddings_enabled(self) -> bool:
        return self.gemini_api_key is not None and bool(self.gemini_api_key.get_secret_value())

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.web_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
