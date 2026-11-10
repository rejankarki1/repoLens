"""Showcase answer schemas."""

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.conversation import CitationResponse


class ShowcaseAnswerResponse(BaseModel):
    id: int
    question: str
    answer: str
    citations: list[CitationResponse]

    model_config = ConfigDict(from_attributes=True)
