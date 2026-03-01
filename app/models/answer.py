from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class AnswerStatus(str, Enum):
    success = "success"
    attempt = "attempt"
    failure = "failure"


class AnswerCreateRequest(BaseModel):
    """Request body for creating an answer."""
    body: str = Field(..., min_length=1, max_length=50000)
    status: AnswerStatus


class AnswerPublic(BaseModel):
    """Public answer data."""
    id: str
    body: str
    question_id: str
    author_id: str
    author_username: str
    status: str
    upvote_count: int
    downvote_count: int
    score: int
    created_at: datetime
    user_vote: str | None = None  # "up", "down", or None (not voted / not authenticated)
    solana_tx: str | None = None
    solana_tx_url: str | None = None
    solana_pda: str | None = None
    solana_pda_url: str | None = None


class AnswerListResponse(BaseModel):
    """Paginated list of answers."""
    answers: list[AnswerPublic]
    page: int
    total_pages: int
