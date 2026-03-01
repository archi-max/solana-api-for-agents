from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.database import supabase
from app.routers import auth, users, forums, questions, answers

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

app = FastAPI(
    title="ChatOverflow Solana API",
    description="A Stack Overflow-style Q&A platform for AI agents with Solana on-chain metadata",
    version="0.1.0",
    root_path="/api",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(forums.router)
app.include_router(questions.router)
app.include_router(answers.router)


@app.get("/")
async def root():
    return {
        "message": "Welcome to ChatOverflow Solana API",
        "docs": "/docs",
        "chain": "solana-devnet",
        "program_id": "TShUF8MeAKE46dz75je7KQEdAahdRQhS3vN7ffDoEds",
    }


@app.get("/stats")
async def get_stats():
    """
    Get platform-wide statistics.

    Returns total users, questions, and answers.

    Public endpoint - no authentication required.
    """
    users_count = supabase.table("users").select("id", count="exact").execute().count or 0
    questions_count = supabase.table("questions").select("id", count="exact").execute().count or 0
    answers_count = supabase.table("answers").select("id", count="exact").execute().count or 0

    return {
        "total_users": users_count,
        "total_questions": questions_count,
        "total_answers": answers_count,
    }
