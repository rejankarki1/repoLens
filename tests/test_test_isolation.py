"""Guards the test setup itself: tests must never be able to write to the development database."""

from apps.api.core import database


def test_the_apps_session_factory_is_bound_to_the_disposable_test_database() -> None:
    assert database.session_factory.kw["bind"].url.database == "repolens_test"
