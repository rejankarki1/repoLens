"""Guards the test setup itself: tests must never be able to write to the development database."""

from apps.api.core import database


def test_the_apps_session_factory_is_bound_to_the_disposable_test_database() -> None:
    assert database.session_factory.kw["bind"].url.database == "repolens_test"


def test_redis_and_the_job_queue_are_the_test_ones_never_the_development_ones() -> None:
    from apps.api.config import get_settings

    settings = get_settings()
    assert settings.redis_url.endswith("/15")  # not db 0, where the developer's real queue lives
    assert settings.ingestion_queue_name == "repolens-test-ingestion"  # not "ingestion"
