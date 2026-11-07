"""scripts/dev.py: how the two env files merge, what children inherit, and which misconfigurations it refuses.

Pure functions only: no processes are started, no network, no real credentials (the values below are made up).
"""

from pathlib import Path

import pytest

from scripts import dev

GOOD_ID = "Ov23liAAAAAAAAAAAAAA"
GOOD_SECRET = "a" * 40

BASE = """\
POSTGRES_USER=repolens
DATABASE_URL=postgresql+asyncpg://repolens:pw@127.0.0.1:5433/repolens
REDIS_URL=redis://127.0.0.1:6379/0
GEMINI_API_KEY=fake-gemini
GROQ_API_KEY=fake-groq
GENERATION_PROVIDER=groq
GROQ_GENERATION_MODEL=openai/gpt-oss-120b
WEB_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
"""
OVERLAY = f"""\
DATABASE_URL=postgresql+asyncpg://repolens:pw@127.0.0.1:5433/repolens_oauth_check
REDIS_URL=redis://127.0.0.1:6379/8
INGESTION_QUEUE_NAME=oauth_check
GITHUB_OAUTH_CLIENT_ID={GOOD_ID}
GITHUB_OAUTH_CLIENT_SECRET={GOOD_SECRET}
WEB_BASE_URL=http://localhost:3000
WEB_ORIGINS=http://localhost:3000
API_PUBLIC_URL=http://localhost:8765
"""


@pytest.fixture
def files(tmp_path: Path):
    base, overlay = tmp_path / ".env", tmp_path / ".env.oauth-check"
    base.write_text(BASE)
    overlay.write_text(OVERLAY)
    return base, overlay


def merged_with(files, **changes) -> dict[str, str]:
    merged = dev.load_env(*files)
    merged.update(changes)
    return merged


def test_the_overlay_wins_and_the_base_supplies_everything_else(files) -> None:
    merged = dev.load_env(*files)
    assert dev._db_name(merged["DATABASE_URL"]) == "repolens_oauth_check"  # overlay beats base
    assert merged["REDIS_URL"].endswith("/8") and merged["INGESTION_QUEUE_NAME"] == "oauth_check"
    assert merged["WEB_ORIGINS"] == "http://localhost:3000"  # overlay replaces the base value, it does not append
    assert merged["GEMINI_API_KEY"] == "fake-gemini" and merged["GENERATION_PROVIDER"] == "groq"  # falls through from the base


def test_a_missing_file_is_an_error_not_a_silent_default(tmp_path: Path, files) -> None:
    with pytest.raises(FileNotFoundError):
        dev.load_env(files[0], tmp_path / "missing")


def test_children_get_only_the_merged_config_never_the_callers_stale_exports(files) -> None:
    parent = {"PATH": "/usr/bin", "HOME": "/home/x", "GEMINI_API_KEY": "stale-shell-key", "DATABASE_URL": "postgresql://stale/db",
              "INGESTION_QUEUE_NAME": "ingestion", "GITHUB_OAUTH_CLIENT_ID": "stale", "SOME_OTHER": "1"}
    env = dev.service_env(dev.load_env(*files), parent)
    assert env["GEMINI_API_KEY"] == "fake-gemini"  # from .env, not from the shell
    assert dev._db_name(env["DATABASE_URL"]) == "repolens_oauth_check" and env["INGESTION_QUEUE_NAME"] == "oauth_check"
    assert env["GITHUB_OAUTH_CLIENT_ID"] == GOOD_ID
    assert "SOME_OTHER" not in env and env["PATH"] == "/usr/bin" and env[dev.STACK_MARKER] == "1"


def test_a_stale_export_cannot_appear_when_the_files_do_not_define_it(files) -> None:
    env = dev.service_env(dev.load_env(*files), {"PATH": "/usr/bin", "GITHUB_TOKEN": "stale-token", "ENVIRONMENT": "production"})
    assert "GITHUB_TOKEN" not in env and "ENVIRONMENT" not in env


def test_api_and_worker_get_the_identical_environment(files) -> None:
    merged = dev.load_env(*files)
    parent = {"PATH": "/usr/bin", "HOME": "/h"}
    assert dev.service_env(merged, parent) == dev.service_env(merged, parent)


def test_the_web_app_holds_no_secrets(files) -> None:
    env = dev.web_env({"PATH": "/usr/bin", "GEMINI_API_KEY": "stale", "GITHUB_OAUTH_CLIENT_SECRET": "stale"})
    assert env["NEXT_PUBLIC_API_URL"] == "http://localhost:8765"
    assert not [k for k in env if "KEY" in k or "SECRET" in k or "TOKEN" in k or "DATABASE" in k]


def test_a_correct_configuration_passes(files) -> None:
    problems, warnings = dev.check_config(dev.load_env(*files), dict(dev.dotenv_values(files[1])))
    assert problems == [] and warnings == []


@pytest.mark.parametrize(
    "change, needle",
    [
        ({"DATABASE_URL": "postgresql+asyncpg://repolens:pw@127.0.0.1:5433/repolens"}, "repolens_oauth_check"),
        ({"DATABASE_URL": "postgresql+asyncpg://repolens:pw@db.example.com:5433/repolens_oauth_check"}, "this machine"),
        ({"REDIS_URL": "redis://127.0.0.1:6379/0"}, "database 8"),
        ({"REDIS_URL": "redis://127.0.0.1:6379"}, "database 8"),
        ({"INGESTION_QUEUE_NAME": "ingestion"}, "oauth_check"),
        ({"INGESTION_QUEUE_NAME": ""}, "oauth_check"),
        ({"GITHUB_OAUTH_CLIENT_ID": "PASTE_MY_CLIENT_ID_HERE"}, "placeholder"),
        ({"GITHUB_OAUTH_CLIENT_ID": ""}, "placeholder"),
        ({"GITHUB_OAUTH_CLIENT_ID": "P" + GOOD_ID}, "wrong shape"),  # the stray leading character that caused the GitHub 404
        ({"GITHUB_OAUTH_CLIENT_SECRET": "PASTE_MY_CLIENT_SECRET_HERE"}, "placeholder"),
        ({"GITHUB_OAUTH_CLIENT_SECRET": "z" * 40}, "wrong shape"),
        ({"GITHUB_OAUTH_CLIENT_SECRET": GOOD_SECRET + "\n"}, "wrong shape"),
        ({"WEB_BASE_URL": "http://127.0.0.1:3000"}, "WEB_BASE_URL"),
        ({"API_PUBLIC_URL": "http://127.0.0.1:8765"}, "API_PUBLIC_URL"),
        ({"WEB_ORIGINS": "http://localhost:3000,http://127.0.0.1:3000"}, "127.0.0.1"),
        ({"WEB_ORIGINS": "http://localhost:4000"}, "WEB_ORIGINS must include"),
        ({"ENVIRONMENT": "production"}, "production"),
        ({"GEMINI_API_KEY": ""}, "GEMINI_API_KEY"),
        ({"GROQ_API_KEY": ""}, "GROQ_API_KEY"),
        ({"GENERATION_PROVIDER": "openai"}, "GENERATION_PROVIDER"),
    ],
)
def test_each_misconfiguration_is_refused_with_a_specific_message(files, change, needle) -> None:
    problems, _ = dev.check_config(merged_with(files, **change))
    assert any(needle in p for p in problems), problems


def test_ai_keys_in_the_overlay_are_flagged_because_they_would_shadow_the_base(files) -> None:
    overlay = dict(dev.dotenv_values(files[1]))
    overlay.update({"GEMINI_API_KEY": "", "GITHUB_TOKEN": ""})
    _, warnings = dev.check_config(dev.load_env(*files), overlay)
    assert any("GEMINI_API_KEY" in w for w in warnings) and any("GITHUB_TOKEN" in w for w in warnings)


def test_problem_messages_never_contain_secret_values(files) -> None:
    merged = merged_with(files, GITHUB_OAUTH_CLIENT_SECRET="s3cr3t-value-that-is-not-40-hex", GEMINI_API_KEY="")
    problems, _ = dev.check_config(merged)
    assert problems and not any("s3cr3t" in p or "fake-groq" in p for p in problems)


def test_alembic_revisions_are_parsed_from_noisy_output() -> None:
    noisy = "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\nINFO  [alembic.runtime.migration] Will assume transactional DDL.\nd2b8f4e0a513 (head)\n"
    assert dev.alembic_revisions(noisy) == ["d2b8f4e0a513"]
    assert dev.alembic_revisions("INFO something\n") == []
    assert dev.alembic_revisions("b84f2c6d1e07\n") == ["b84f2c6d1e07"]  # `current` on an out-of-date database


def test_process_environment_is_read_by_name_only() -> None:
    ps = "99 ttys001 S 0:01 python -m apps.api.worker PATH=/x DATABASE_URL=postgresql+asyncpg://u:pw@127.0.0.1:5433/repolens REDIS_URL=redis://127.0.0.1:6379/8 INGESTION_QUEUE_NAME=oauth_check"
    env = dev.pick_env(ps, ("DATABASE_URL", "REDIS_URL", "INGESTION_QUEUE_NAME", "GEMINI_API_KEY"))
    assert "GEMINI_API_KEY" not in env
    summary = dev.describe_config(env)
    assert summary == "database=repolens redis_db=8 queue=oauth_check" and "pw" not in summary
    assert dev.describe_config({}) == "database=? redis_db=0 (default) queue=ingestion (default)"  # what a plain-command process looks like


# --- separately started services: same config, visible proof, no secrets in what they print ---------------------------

def test_the_config_fingerprint_is_stable_and_changes_with_any_value(files) -> None:
    merged = dev.load_env(*files)
    assert dev.fingerprint(merged) == dev.fingerprint(dict(reversed(list(merged.items()))))  # order does not matter
    assert len(dev.fingerprint(merged)) == 8
    for key in ("DATABASE_URL", "GEMINI_API_KEY", "INGESTION_QUEUE_NAME"):
        assert dev.fingerprint({**merged, key: merged[key] + "x"}) != dev.fingerprint(merged)


def test_api_and_worker_started_separately_get_the_same_environment_and_fingerprint(files) -> None:
    merged = dev.load_env(*files)
    # two independent launches, different shells with different stale exports
    api = dev.service_env(merged, {"PATH": "/usr/bin", "GEMINI_API_KEY": "stale-1", "DATABASE_URL": "postgresql://stale/one"})
    worker = dev.service_env(merged, {"PATH": "/usr/bin", "HOME": "/h", "INGESTION_QUEUE_NAME": "ingestion"})
    drop = {"HOME"}  # the only difference allowed: pass-through of the shell's own HOME/PATH-style basics
    assert {k: v for k, v in api.items() if k not in drop} == {k: v for k, v in worker.items() if k not in drop}
    assert dev.banner("api", merged) == dev.banner("api", merged)
    assert dev.fingerprint(merged) in dev.banner("api", merged) and dev.fingerprint(merged) in dev.banner("worker", merged)


def test_the_startup_banner_names_the_providers_but_never_prints_a_credential(files) -> None:
    merged = dev.load_env(*files)
    text = dev.banner("worker", merged)
    for secret in ("fake-gemini", "fake-groq", GOOD_SECRET, GOOD_ID, "pw@"):
        assert secret not in text
    assert "groq openai/gpt-oss-120b (key set: yes)" in text and "Gemini key set: yes" in text and "GitHub OAuth configured: yes" in text
    assert "database=repolens_oauth_check redis_db=8 queue=oauth_check" in text


def test_the_banner_says_plainly_when_something_is_missing(files) -> None:
    text = dev.banner("api", {**dev.load_env(*files), "GEMINI_API_KEY": "", "GITHUB_OAUTH_CLIENT_SECRET": ""})
    assert "Gemini key set: NO" in text and "GitHub OAuth configured: NO" in text


@pytest.mark.parametrize(
    "command, is_worker",
    [
        ("/opt/homebrew/Cellar/python@3.14/3.14.5/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python -m apps.api.worker", True),
        ("python -m apps.api.worker", True),
        ("/repo/apps/api/venv/bin/python3.14 -m apps.api.worker", True),
        ("grep apps.api.worker", False),
        ("pgrep -f apps.api.worker", False),
        ("/bin/zsh -c ps -eo pid,command | grep apps.api.worker | awk", False),
        ("tail -f logs/apps.api.worker.log", False),
        ("python -m apps.api.main", False),
    ],
)
def test_only_a_python_running_the_worker_module_counts_as_a_worker(command: str, is_worker: bool) -> None:
    assert bool(dev.WORKER_COMMAND.search(command)) is is_worker
