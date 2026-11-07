"""The Render supervisor: clean stop on SIGTERM, and a visible failure when a child dies."""

import signal
import sys
import threading
import time

from apps.api.config import Settings, normalize_database_url
from apps.api.supervisor import child_commands, run

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]
DIE = [sys.executable, "-c", "import sys, time; time.sleep(0.3); sys.exit(3)"]


def test_one_api_and_one_worker():
    cmds = child_commands("1234")
    assert list(cmds) == ["api", "worker"]
    assert cmds["api"][cmds["api"].index("--workers") + 1] == "1"
    assert cmds["api"][cmds["api"].index("--port") + 1] == "1234" and "0.0.0.0" in cmds["api"]
    assert cmds["worker"][-2:] == ["-m", "apps.api.worker"]


def test_child_exit_stops_the_other_and_fails():
    started = time.monotonic()
    code = run({"api": SLEEP, "worker": DIE}, grace=5, poll=0.05)
    assert code == 3
    assert time.monotonic() - started < 10


def test_sigterm_stops_everything_cleanly():
    timer = threading.Timer(0.5, lambda: signal.raise_signal(signal.SIGTERM))
    timer.start()
    try:
        assert run({"api": SLEEP, "worker": SLEEP}, grace=5, poll=0.05) == 0
    finally:
        timer.cancel()


def test_database_url_is_made_asyncpg_ready():
    neon = "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    assert normalize_database_url(neon) == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"
    assert normalize_database_url("postgres://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"
    local = "postgresql+asyncpg://u:p@127.0.0.1:5433/db"
    assert normalize_database_url(local) == local
    assert Settings(database_url=neon).database_url.startswith("postgresql+asyncpg://")


import pytest
from fastapi import HTTPException

from apps.api.services.quota import refund_import, reserve_import
from apps.api.services.rate_limit import MemoryLimiter


@pytest.mark.anyio
async def test_global_daily_import_cap_spans_users_and_is_refundable():
    settings = Settings(import_global_daily_limit=2, import_hourly_limit=5)
    limiter = MemoryLimiter()
    await reserve_import(limiter, settings, 1)
    await reserve_import(limiter, settings, 2)
    with pytest.raises(HTTPException) as refused:
        await reserve_import(limiter, settings, 3)
    assert refused.value.status_code == 429
    await refund_import(limiter, 2, settings)
    await reserve_import(limiter, settings, 3)  # room again after a refund
    # the refused attempt must not have consumed user 3's own hourly allowance
    for _ in range(4):
        with pytest.raises(HTTPException):
            await reserve_import(limiter, settings, 3)


@pytest.mark.anyio
async def test_global_import_cap_is_off_by_default():
    settings = Settings(import_hourly_limit=100)
    limiter = MemoryLimiter()
    for user in range(50):
        await reserve_import(limiter, settings, user)
