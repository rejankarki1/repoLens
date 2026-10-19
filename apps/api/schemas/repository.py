"""Repository API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RepositoryResponse(BaseModel):
    id: int
    github_repo_id: int
    github_account_id: int | None
    full_name: str
    default_branch: str
    created_at: datetime
    ingestion_status: str
    last_ingested_commit_sha: str | None
    ingestion_truncated: bool
    last_ingested_at: datetime | None
    ingestion_error: str | None

    model_config = ConfigDict(from_attributes=True)


class RepositoryCreate(BaseModel):
    github_url: str = Field(min_length=1)
