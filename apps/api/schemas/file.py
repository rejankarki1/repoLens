"""Source-file API response schemas."""

from pydantic import BaseModel, ConfigDict


class FileSummary(BaseModel):
    path: str
    language: str
    category: str
    size_bytes: int
    line_count: int

    model_config = ConfigDict(from_attributes=True)


class FileContent(FileSummary):
    content: str
