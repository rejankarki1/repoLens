# Code search

Repository-scoped search over the code chunks produced at ingestion: PostgreSQL full-text search and
pgvector similarity, fused with Reciprocal Rank Fusion. Everything is scoped to the repository's
**current** snapshot (`repositories.last_ingested_commit_sha`); other repositories and older commits are
never returned.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /repositories/{id}/search?q=…&limit=10&mode=hybrid` | Search. `mode` is `hybrid` (default), `keyword` or `semantic`. `limit` 1–50, `q` 1–500 characters. |
| `GET /repositories/{id}/search-index` | Readiness: chunks in the snapshot, how many have embeddings, whether an embedding job is running. |
| `POST /repositories/{id}/embeddings` | Queue the embedding job (needed once for repositories ingested before semantic search existed; ingestion queues it automatically afterwards). Safe to repeat. |

Each hit carries `path`, `symbol_name`, `symbols`, `kind`, `start_line`/`end_line` (1-based, inclusive),
`start_byte`/`end_byte` (UTF-8 offsets into the file's stored content, exact even for partial-line chunks),
the chunk `content`, the fusion `score`, and `matched_by` (`keyword`, `exact`, `semantic`) with each rank.

Errors: `404` unknown repository, `409` not ingested yet / no chunks / semantic mode not available,
`422` empty or oversized query, `503` provider outage in `semantic` mode. In `hybrid` mode an outage or a
missing key is **not** an error: the response is keyword-only, with an entry in `warnings`.

## How a query is answered

1. **Keyword.** `code_chunks.search_vector` (GIN) holds the normalized path and symbol names (weight A)
   and the code (weight C). Punctuation is stripped and camelCase is split, so `loadEnv`, `load env` and
   `load_env_local` find `loadEnvLocal`. The query is built from sanitized lexemes only (no user text
   reaches the query parser) and chunks containing *all* terms rank before partial matches.
2. **Exact.** A query that looks like a name (one identifier, or up to four words that spell one) also
   looks up chunks whose primary symbol or file name equals it ignoring case, `_` and punctuation, so
   definitions and same-named files outrank mentions.
3. **Semantic.** The query is embedded (one provider call, cached) and compared by cosine distance with
   the repository's chunk vectors.
4. **Fusion.** `score = Σ 1 / (60 + rank)` over the retrievers that found a chunk. Only ranks are used,
   so keyword scores and distances never need calibrating.

## Embeddings

* **Provider / model:** Google `gemini-embedding-001`, requested at **1,536 dimensions** (`outputDimensionality`;
  the model supports 128-3,072) through the native `batchEmbedContents` endpoint. The OpenAI-compatible
  endpoint is not used because it cannot set a task type. Gemini embeds documents and queries with a *pair* of
  task types, so chunks are embedded as `RETRIEVAL_DOCUMENT` and search queries as `CODE_RETRIEVAL_QUERY`
  (Google's task for finding code from a natural-language query). Vectors below 3,072 dimensions are not unit
  length, so the client L2-normalizes them. Every vector is checked to be exactly 1,536 values; anything else
  is rejected as a permanent provider error and never stored.
* **Cost:** Gemini has a free tier with rate limits and a paid tier priced per million input tokens; check
  Google's current pricing page. A 1,500-byte chunk is roughly 350 tokens, so ClassLens (869 chunks) is about
  0.3M tokens, a few cents at most, and a 10,000-chunk repository about 3.5M tokens. Free-tier rate limits make
  a 429 plausible on large repositories: the job retries (see below) and resumes.
* **Configuration (backend environment only):** `GEMINI_API_KEY` (required to enable semantic search; sent in
  the `x-goog-api-key` header, never in a URL, never logged or returned), `EMBEDDING_MODEL`,
  `EMBEDDING_API_BASE_URL`, `EMBEDDING_BATCH_SIZE` (64; capped at 100, Gemini's batch limit),
  `EMBEDDING_MAX_CHUNKS_PER_REPOSITORY` (10,000 distinct texts per run), `EMBEDDING_QUERY_TIMEOUT_SECONDS` (5).
  `EMBEDDING_DIMENSIONS` must stay 1536 (the column width). `OPENAI_API_KEY` is ignored.
* **Storage:** `code_embeddings(content_hash, model, embedding)`, keyed by the hash of the chunk text and the
  model key `gemini-embedding-001/1536`. Re-ingesting an unchanged chunk, in any commit or repository, reuses
  its vector, a vector can never be attached to different text, and rows for different models never mix.
  If the task types or dimensionality ever change, change the model key too. Searches reach vectors only
  through the current snapshot's chunks. No ANN index yet: scoped scans are fast at this size (add HNSW if a
  repository passes ~100k chunks). Vectors of deleted repositories are not garbage-collected yet.
* **Jobs:** the embedding job runs on the existing RQ queue and worker. Ingestion is already `completed`
  when it is queued, so a missing key or provider outage never affects source ingestion or keyword search.
  The job embeds only missing texts, commits after every batch, and holds a PostgreSQL advisory lock per
  repository (released automatically if the worker dies). Transient provider errors (429, 5xx, network) are
  retried by RQ (30 s, 2 min) and resume where they stopped; permanent ones (invalid key, bad request) are logged.
* **Rate limits (HTTP 429).** Gemini quotas are per project and per model (the free tier is the tightest; the
  figures are in Google AI Studio, not in the public docs). On a 429 the client reads Google's `RetryInfo`
  delay (or `Retry-After`), waits that long (capped at `EMBEDDING_MAX_RATE_LIMIT_WAIT_SECONDS`, 60) and tries
  again, up to `EMBEDDING_RATE_LIMIT_ATTEMPTS` (6) per batch, logging which quota was hit. A delay longer than
  the cap, or six limits in a row, fails the attempt and the job is retried by RQ on its own schedule
  (`EMBEDDING_RETRY_INTERVALS_SECONDS`, default 60 s, 3 min, 5 min, 10 min); finished batches are kept and each
  retry resumes where the last stopped. A **per-day** quota is not retried (it resets at midnight Pacific
  time): the job stops cleanly, and `POST /repositories/{id}/embeddings` finishes the rest later. A search
  never waits on a rate limit; it falls back to keyword results. `POST /embeddings` refuses (409) while an
  embedding job is already queued, retrying or running.
* **Readiness** is derived from the data, not a second state machine: `search-index` reports
  `disabled` (no key), `not_built`, `partial` or `ready`.

## Operations

* Apply migration `11c177d30e38` before ingesting or searching; it backfills the keyword index for chunks
  that already exist.
* To enable semantic search: set `GEMINI_API_KEY` in `.env`, restart the API and the worker, then call
  `POST /repositories/{id}/embeddings` for repositories ingested earlier.
* After a hard worker crash (`kill -9`), RQ keeps its scheduler lock for about a minute, and a worker
  restarted inside that window runs without a scheduler (retries wait). Restart it again after a minute.
