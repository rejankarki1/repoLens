"""Run the API and the one RQ worker in a single container (the Render Free demo arrangement).

    python -m apps.api.supervisor

Both children are started in their own process group, so a stop signal reaches the worker's job subprocesses as
well. SIGTERM/SIGINT stops them cleanly (the worker is asked to finish its current job, then killed after a grace
period). If either child exits on its own the other is stopped and this process exits non-zero, so the platform
restarts the whole service instead of leaving a half-working one running. Exactly one worker is ever started.
"""

import logging
import os
import signal
import subprocess
import sys
import time

logger = logging.getLogger("supervisor")
GRACE_SECONDS = 25.0  # Render sends SIGKILL 30 s after SIGTERM


def child_commands(port: str, python: str = sys.executable) -> dict[str, list[str]]:
    return {
        "api": [python, "-m", "uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", port, "--workers", "1"],
        "worker": [python, "-m", "apps.api.worker"],
    }


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stop_all(procs: dict[str, subprocess.Popen], grace: float = GRACE_SECONDS) -> None:
    for proc in procs.values():
        if proc.poll() is None:
            _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + grace
    for name, proc in procs.items():
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            logger.warning("%s did not stop in time; killing it", name)
            _signal_group(proc, signal.SIGKILL)
            proc.wait()


def run(commands: dict[str, list[str]], grace: float = GRACE_SECONDS, poll: float = 0.5) -> int:
    stopping: list[int] = []
    previous = {s: signal.signal(s, lambda signum, _f: stopping.append(signum)) for s in (signal.SIGTERM, signal.SIGINT)}
    procs: dict[str, subprocess.Popen] = {}
    try:
        for name, cmd in commands.items():
            procs[name] = subprocess.Popen(cmd, start_new_session=True)
            logger.info("started %s (pid %s)", name, procs[name].pid)
        while not stopping:
            for name, proc in procs.items():
                if (code := proc.poll()) is not None:
                    logger.error("%s exited with status %s; shutting the service down", name, code)
                    stop_all(procs, grace)
                    return code or 1
            time.sleep(poll)
        logger.info("received signal %s; stopping", stopping[0])
        stop_all(procs, grace)
        return 0
    finally:
        if any(p.poll() is None for p in procs.values()):  # only reached on an unexpected error
            stop_all(procs, 0.0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(run(child_commands(os.environ.get("PORT", "8000"))))


if __name__ == "__main__":
    main()
