from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

    Returns counts, vote totals, on-chain anchoring stats, and token economics.

    Public endpoint - no authentication required.
    """
    users_count = supabase.table("users").select("id", count="exact").execute().count or 0
    questions_count = supabase.table("questions").select("id", count="exact").execute().count or 0
    answers_count = supabase.table("answers").select("id", count="exact").execute().count or 0
    forums_count = supabase.table("forums").select("id", count="exact").execute().count or 0

    # Vote counts
    question_votes_count = supabase.table("question_votes").select("user_id", count="exact").execute().count or 0
    answer_votes_count = supabase.table("answer_votes").select("user_id", count="exact").execute().count or 0

    # Upvotes specifically (each mints 10 $OVERFLOW)
    q_upvotes = supabase.table("question_votes").select("user_id", count="exact").eq("vote_type", "up").execute().count or 0
    a_upvotes = supabase.table("answer_votes").select("user_id", count="exact").eq("vote_type", "up").execute().count or 0
    total_upvotes = q_upvotes + a_upvotes
    tokens_minted = total_upvotes * 10  # 10 $OVERFLOW per upvote

    # On-chain anchoring stats
    questions_on_chain = supabase.table("questions").select("id", count="exact").neq("solana_tx", "null").not_.is_("solana_tx", "null").execute().count or 0
    answers_on_chain = supabase.table("answers").select("id", count="exact").neq("solana_tx", "null").not_.is_("solana_tx", "null").execute().count or 0

    return {
        "total_users": users_count,
        "total_forums": forums_count,
        "total_questions": questions_count,
        "total_answers": answers_count,
        "total_votes": question_votes_count + answer_votes_count,
        "total_upvotes": total_upvotes,
        "tokens_minted": tokens_minted,
        "token_symbol": "$OVERFLOW",
        "tokens_per_upvote": 10,
        "questions_on_chain": questions_on_chain,
        "answers_on_chain": answers_on_chain,
        "chain": "solana-devnet",
        "program_id": "TShUF8MeAKE46dz75je7KQEdAahdRQhS3vN7ffDoEds",
    }
