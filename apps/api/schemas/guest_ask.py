"""Guest Ask AI status schema."""

from pydantic import BaseModel


class GuestAskStatus(BaseModel):
    enabled: bool  # the server allows guest questions at all (and answers are configured)
    limit: int  # questions per visitor per day
    remaining: int  # what this visitor can still ask: the smallest of the visitor, address and all-guests allowances
    resets_in_seconds: int | None  # until the window that limits `remaining` reopens; None while nothing is used
