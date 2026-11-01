"""Shared schema helpers."""

from typing import Annotated

from pydantic import AfterValidator


def _no_nul(value: str) -> str:
    """PostgreSQL text cannot contain a NUL byte; refuse it at the boundary instead of failing in the database."""
    if "\x00" in value:
        raise ValueError("must not contain a NUL character")
    return value


NulFreeStr = Annotated[str, AfterValidator(_no_nul)]
