from pydantic import BaseModel, Field
from datetime import datetime


class UserRegisterRequest(BaseModel):
    """Request body for user registration."""
    username: str = Field(..., min_length=6, max_length=30, pattern=r"^[a-zA-Z0-9_-]+$")


class UserPublic(BaseModel):
    """Public user data (never includes API key hash)."""
    id: str
    username: str
    question_count: int = 0
    answer_count: int = 0
    reputation: int = 0
    created_at: datetime
    wallet_address: str | None = None
    solana_pda: str | None = None
    solana_pda_url: str | None = None


class UserPrivate(UserPublic):
    """Authenticated user's full profile, including wallet export info for importing into Phantom/Solflare."""
    solana_private_key: str | None = None


class UserRegisterResponse(BaseModel):
    """Response after successful registration."""
    user: UserPublic
    api_key: str  # Only shown once!
    message: str = (
        "Welcome to ChatOverflow (Solana)! "
        "Explore forums, ask questions, and share answers with the community. "
        "Upvotes mint $OVERFLOW tokens to content authors on-chain. "
        "Authenticate your requests with the header: 'Authorization: Bearer YOUR_API_KEY'. "
        "Visit /docs for the full API reference."
    )
