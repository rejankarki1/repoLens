"""Repository API request/response schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import NulFreeStr


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
    # "member": in your workspace. "guest": a showcase repository you may only browse. Set by the API per caller.
    viewer_access: Literal["member", "guest"] | None = None

    model_config = ConfigDict(from_attributes=True)


class RepositoryCreate(BaseModel):
    github_url: NulFreeStr = Field(min_length=1, max_length=2048)


class RepositoryRemoval(BaseModel):
    repository_id: int
    removed: bool = True
    # False when other members (or the guest showcase) still use the stored repository data.
    data_deleted: bool
