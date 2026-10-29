"""RQ queue configuration for background ingestion.

RQ needs a *synchronous* Redis client, separate from the asyncio client used elsewhere in the API.
"""

from datetime import datetime, timedelta, timezone
from functools import lru_cache

from redis import Redis
from rq import Queue, Retry, Worker
from rq.job import Job
from rq.registry import ScheduledJobRegistry, StartedJobRegistry

from apps.api.config import Settings, get_settings
from apps.api.jobs.ingestion_job import ingest_repository_job


def get_redis_connection(settings: Settings | None = None) -> Redis:
    return Redis.from_url((settings or get_settings()).redis_url)


def get_ingestion_queue(connection: Redis | None = None, settings: Settings | None = None) -> Queue:
    settings = settings or get_settings()
    return Queue(settings.ingestion_queue_name, connection=connection or get_redis_connection(settings))


@lru_cache
def _shared_queue() -> Queue:
    return get_ingestion_queue()


def get_queue() -> Queue:
    """FastAPI dependency: the process-wide ingestion queue (tests override this)."""
    return _shared_queue()


INGESTION_JOB_FUNC = "apps.api.jobs.ingestion_job.ingest_repository_job"
EMBEDDING_JOB_FUNC = "apps.api.jobs.embedding_job.embed_repository_job"
ARCHITECTURE_JOB_FUNC = "apps.api.jobs.architecture_job.analyze_architecture_job"


def _worker_is_alive(name: str | None, connection: Redis, max_age_seconds: float) -> bool:
    """Whether the named RQ worker has refreshed its own heartbeat within `max_age_seconds`.

    RQ keeps a dead worker's Redis key for `worker_ttl + 60` seconds, which is too slow to wait for,
    so this checks the heartbeat's age instead. A worker that is running a job refreshes it every
    `job_monitoring_interval` (see `worker.build_worker`), well inside the threshold.
    """
    if not name:
        return False
    worker = Worker.find_by_key(f"{Worker.redis_worker_namespace_prefix}{name}", connection=connection)
    heartbeat = worker.last_heartbeat if worker else None
    if heartbeat is None:
        return False
    return datetime.now(timezone.utc) - heartbeat <= timedelta(seconds=max_age_seconds)


def live_repository_ids(
    queue: Queue, settings: Settings | None = None, job_func: str = INGESTION_JOB_FUNC
) -> set[int]:
    """Repositories RQ still has a live job of `job_func` for (ingestion by default): waiting, scheduled for retry, or running.

    A running job only counts while the worker that started it is alive (recent heartbeat), so a
    crashed worker's leftover registry entry does not protect its repository. Raises
    `redis.RedisError` if Redis is unreachable; callers should then skip recovery rather than guess.
    """
    max_age = (settings or get_settings()).ingestion_worker_ttl_seconds
    started_ids = set(StartedJobRegistry(queue=queue).get_job_ids())
    job_ids = set(queue.job_ids) | set(ScheduledJobRegistry(queue=queue).get_job_ids()) | started_ids
    live: set[int] = set()
    for job in Job.fetch_many(job_ids, connection=queue.connection):
        if job is None or job.func_name != job_func or not job.args:
            continue
        if job.id in started_ids and not _worker_is_alive(job.worker_name, queue.connection, max_age):
            continue
        live.add(int(job.args[0]))
    return live


def enqueue_embedding(queue: Queue, repository_id: int, settings: Settings | None = None) -> Job:
    """Queue an embedding job on the same queue and worker as ingestion (same timeout and retry policy)."""
    from apps.api.jobs.embedding_job import embed_repository_job

    settings = settings or get_settings()
    intervals = settings.embedding_retry_intervals_seconds
    return queue.enqueue(
        embed_repository_job,
        repository_id,
        job_timeout=settings.ingestion_job_timeout_seconds,
        failure_ttl=settings.ingestion_failure_ttl_seconds,
        retry=Retry(max=len(intervals), interval=intervals) if intervals else None,
    )


def enqueue_ingestion(queue: Queue, repository_id: int, settings: Settings | None = None) -> Job:
    """Queue one ingestion. Transient failures retry after each configured interval (30 s, then 2 min).

    Interval retries are scheduled by RQ, so the worker must run with its scheduler enabled.
    """
    settings = settings or get_settings()
    intervals = settings.ingestion_retry_intervals_seconds
    return queue.enqueue(
        ingest_repository_job,
        repository_id,
        job_timeout=settings.ingestion_job_timeout_seconds,
        failure_ttl=settings.ingestion_failure_ttl_seconds,
        retry=Retry(max=len(intervals), interval=intervals) if intervals else None,
    )


def enqueue_architecture(queue: Queue, repository_id: int, settings: Settings | None = None) -> Job:
    """Queue an architecture analysis on the same queue and worker as ingestion.

    No RQ retry: the analysis is deterministic, so a failure is recorded on its row and the user re-requests it.
    """
    from apps.api.jobs.architecture_job import analyze_architecture_job

    settings = settings or get_settings()
    return queue.enqueue(
        analyze_architecture_job,
        repository_id,
        job_timeout=settings.ingestion_job_timeout_seconds,
        failure_ttl=settings.ingestion_failure_ttl_seconds,
    )
