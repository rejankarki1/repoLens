"""Guards the test setup itself: no test may reach development data, the real job queue or a paid provider."""

from sqlalchemy.engine import make_url

from apps.api.config import Settings, get_settings
from apps.api.core import database


def _db(url: str) -> str | None:
    return make_url(url).database


def test_every_reader_of_the_database_setting_gets_the_disposable_test_database() -> None:
    assert _db(get_settings().database_url) == "repolens_test"  # job entry points, Alembic, admin helpers
    assert _db(Settings().database_url) == "repolens_test"  # settings objects built inside tests
    assert _db(str(database.engine.url)) == "repolens_test"  # the app's own engine
    assert database.session_factory.kw["bind"].url.database == "repolens_test"


def test_redis_and_the_job_queue_are_the_test_ones_never_the_development_ones() -> None:
    settings = get_settings()
    assert settings.redis_url.endswith("/15")  # not db 0, where the developer's real queue lives
    assert settings.ingestion_queue_name == "repolens-test-ingestion"  # not "ingestion"


def test_no_real_provider_or_github_credential_is_available_to_tests() -> None:
    settings = get_settings()
    assert not settings.embeddings_enabled
    assert not (settings.groq_api_key and settings.groq_api_key.get_secret_value())
    assert not settings.github_token
    assert not (settings.github_oauth_client_secret and settings.github_oauth_client_secret.get_secret_value())
