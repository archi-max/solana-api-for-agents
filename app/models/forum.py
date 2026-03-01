from pydantic import BaseModel, Field
from datetime import datetime


class ForumCreateRequest(BaseModel):
    """Request body for creating a forum."""
    name: str = Field(..., min_length=1, max_length=250)
    description: str | None = Field(None, max_length=50000)


class ForumPublic(BaseModel):
    """Public forum data."""
    id: str
    name: str
    description: str | None
    created_by: str
    created_by_username: str
    question_count: int
    created_at: datetime
    solana_tx: str | None = None
    solana_tx_url: str | None = None
    solana_pda: str | None = None
    solana_pda_url: str | None = None


class ForumListResponse(BaseModel):
    """Paginated list of forums."""
    forums: list[ForumPublic]
    page: int
    total_pages: int
