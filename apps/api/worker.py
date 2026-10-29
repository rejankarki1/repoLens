"""Standalone RQ worker for background ingestion.

Run from the repository root:

    apps/api/venv/bin/python -m apps.api.worker

macOS uses `SpawnWorker` (fresh process per job, avoiding fork-safety problems); Linux uses the
standard forking `Worker`. The scheduler is enabled because retry backoff is scheduled by RQ.
"""

import logging
import sys

from rq import Queue, Worker
from rq.worker import SpawnWorker

from apps.api.config import get_settings
from apps.api.core.queue import get_ingestion_queue
from apps.api.jobs.maintenance import run_startup_maintenance
from apps.api.services.code_parser import missing_grammars


def worker_class() -> type[Worker]:
    return SpawnWorker if sys.platform == "darwin" else Worker


def build_worker(queue: Queue) -> Worker:
    ttl = get_settings().ingestion_worker_ttl_seconds
    return worker_class()(
        [queue],
        connection=queue.connection,
        worker_ttl=ttl,
        # RQ refreshes the worker's registration this often while a job runs; keep it well under the TTL
        # so a live worker is never mistaken for a dead one.
        job_monitoring_interval=max(1, ttl // 3),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    missing = missing_grammars()
    if missing:  # never downloaded while processing a repository: those files use the text fallback
        logging.getLogger(__name__).warning("Tree-sitter grammars not installed: %s. Run ensure_grammars().", missing)
    run_startup_maintenance()
    build_worker(get_ingestion_queue()).work(with_scheduler=True)


if __name__ == "__main__":
    main()
