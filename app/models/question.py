from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum


class SortOption(str, Enum):
    newest = "newest"
    top = "top"


class VoteOption(str, Enum):
    up = "up"
    down = "down"
    none = "none"


class VoteRequest(BaseModel):
    """Request body for voting on a question."""
    vote: VoteOption


class QuestionCreateRequest(BaseModel):
    """Request body for creating a question."""
    title: str = Field(..., min_length=1, max_length=250)
    body: str = Field(..., min_length=1, max_length=50000)
    forum_id: str


class QuestionPublic(BaseModel):
    """Public question data."""
    id: str
    title: str
    body: str
    forum_id: str
    forum_name: str
    author_id: str
    author_username: str
    upvote_count: int
    downvote_count: int
    score: int
    answer_count: int
    created_at: datetime
    user_vote: str | None = None  # "up", "down", or None (not voted / not authenticated)
    solana_tx: str | None = None
    solana_pda: str | None = None


class QuestionListResponse(BaseModel):
    """Paginated list of questions."""
    questions: list[QuestionPublic]
    page: int
    total_pages: int
