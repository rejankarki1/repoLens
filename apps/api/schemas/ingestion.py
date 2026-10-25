"""Ingestion API response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IngestionStatusResponse(BaseModel):
    repository_id: int
    status: str
    commit_sha: str | None
    truncated: bool
    last_ingested_at: datetime | None
    error: str | None

    model_config = ConfigDict(from_attributes=True)

