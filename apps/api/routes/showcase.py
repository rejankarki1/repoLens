"""Curated answers for showcase repositories: read-only, served from the database, no AI involved."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import get_db
from apps.api.core.limits import guest_read_limit
from apps.api.models.entities import ShowcaseAnswer
from apps.api.schemas.showcase import ShowcaseAnswerResponse
from apps.api.services.access import RepoAccess, read_access

router = APIRouter(tags=["showcase"])


@router.get("/repositories/{repository_id}/showcase-answers", response_model=list[ShowcaseAnswerResponse])
async def list_showcase_answers(
    db: AsyncSession = Depends(get_db),
    access: RepoAccess = Depends(read_access),
    _guest_limit: None = Depends(guest_read_limit),
) -> list[ShowcaseAnswerResponse]:
    """Published example questions and answers; only showcase repositories have any (members of others get `[]`)."""
    rows = await db.execute(
        select(ShowcaseAnswer).where(ShowcaseAnswer.repository_id == access.repository.id).order_by(ShowcaseAnswer.position, ShowcaseAnswer.id)
    )
    return [ShowcaseAnswerResponse.model_validate(row) for row in rows.scalars()]
