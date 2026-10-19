"""Environment-based configuration for the API."""

from functools import lru_cache

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.web_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
