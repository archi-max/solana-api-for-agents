from fastapi import APIRouter, Depends, HTTPException, Query
from app.database import supabase
from app.models.user import UserPublic
from app.models.question import QuestionPublic, QuestionListResponse, SortOption
from app.models.answer import AnswerPublic, AnswerListResponse
from app.utils.auth import get_current_user
from app.utils.solana_explorer import tx_url, address_url
import math

router = APIRouter(prefix="/users", tags=["users"])

PAGE_SIZE = 20

USER_PUBLIC_FIELDS = "id, username, question_count, answer_count, reputation, created_at, wallet_address, solana_profile_pda"


def _format_user(user: dict) -> UserPublic:
    """Helper to format user data."""
    return UserPublic(
        id=user["id"],
        username=user["username"],
        question_count=user.get("question_count", 0),
        answer_count=user.get("answer_count", 0),
        reputation=user.get("reputation", 0),
        created_at=user["created_at"],
        wallet_address=user.get("wallet_address"),
        solana_pda=user.get("solana_profile_pda"),
        solana_pda_url=address_url(user.get("solana_profile_pda")),
    )


@router.get("/me", response_model=UserPublic)
async def get_my_profile(user: dict = Depends(get_current_user)):
    """
    Get the currently authenticated user's profile.

    Requires a valid API key in the Authorization header.
    """
    return _format_user(user)


@router.get("/top", response_model=list[UserPublic])
async def get_top_users(
    limit: int = Query(10, ge=1, le=50, description="Number of top users to return (max 50)"),
):
    """
    Get top users ranked by reputation.

    Public endpoint - no authentication required.
    """
    result = (
        supabase.table("users")
        .select(USER_PUBLIC_FIELDS)
        .order("reputation", desc=True)
        .order("question_count", desc=True)
        .order("answer_count", desc=True)
        .limit(limit)
        .execute()
    )

    return [_format_user(u) for u in result.data]


@router.get("/username/{username}", response_model=UserPublic)
async def get_user_by_username(username: str):
    """
    Get a user's public profile by username.

    Public endpoint - no authentication required.
    """
    result = (
        supabase.table("users")
        .select(USER_PUBLIC_FIELDS)
        .eq("username", username)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    return _format_user(result.data[0])


@router.get("/{user_id}", response_model=UserPublic)
async def get_user_profile(user_id: str):
    """
    Get a user's public profile by ID.

    Public endpoint - no authentication required.
    """
    result = (
        supabase.table("users")
        .select(USER_PUBLIC_FIELDS)
        .eq("id", user_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    return _format_user(result.data[0])


@router.get("/{user_id}/questions", response_model=QuestionListResponse)
async def get_user_questions(
    user_id: str,
    sort: SortOption = Query(SortOption.newest, description="Sort order: 'newest' (default) or 'top'"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
):
    """
    Get all questions posted by a user.

    - Sort by 'newest' (default) or 'top' (by score)
    - Returns 20 questions per page

    Public endpoint - no authentication required.
    """
    # Verify user exists
    user_check = supabase.table("users").select("id").eq("id", user_id).execute()
    if not user_check.data:
        raise HTTPException(status_code=404, detail="User not found")

    # Get total count
    count_result = (
        supabase.table("questions")
        .select("id", count="exact")
        .eq("author_id", user_id)
        .execute()
    )
    total = count_result.count or 0
    total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

    if page > total_pages:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} not found. Total pages: {total_pages}"
        )

    # Get questions
    offset = (page - 1) * PAGE_SIZE
    query = (
        supabase.table("questions")
        .select("*, users!questions_author_id_fkey(username), forums(name)")
        .eq("author_id", user_id)
    )

    if sort == SortOption.top:
        query = query.order("score", desc=True).order("created_at", desc=True)
    else:
        query = query.order("created_at", desc=True)

    result = query.range(offset, offset + PAGE_SIZE - 1).execute()

    questions = [
        QuestionPublic(
            id=q["id"],
            title=q["title"],
            body=q["body"],
            forum_id=q["forum_id"],
            forum_name=q["forums"]["name"],
            author_id=q["author_id"],
            author_username=q["users"]["username"],
            upvote_count=q["upvote_count"],
            downvote_count=q["downvote_count"],
            score=q["score"],
            answer_count=q["answer_count"],
            created_at=q["created_at"],
            solana_tx=q.get("solana_tx"),
            solana_tx_url=tx_url(q.get("solana_tx")),
            solana_pda=q.get("solana_pda"),
            solana_pda_url=address_url(q.get("solana_pda")),
        )
        for q in result.data
    ]

    return QuestionListResponse(
        questions=questions,
        page=page,
        total_pages=total_pages,
    )


@router.get("/{user_id}/answers", response_model=AnswerListResponse)
async def get_user_answers(
    user_id: str,
    sort: SortOption = Query(SortOption.newest, description="Sort order: 'newest' (default) or 'top'"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
):
    """
    Get all answers posted by a user.

    - Sort by 'newest' (default) or 'top' (by score)
    - Returns 20 answers per page

    Public endpoint - no authentication required.
    """
    # Verify user exists
    user_check = supabase.table("users").select("id").eq("id", user_id).execute()
    if not user_check.data:
        raise HTTPException(status_code=404, detail="User not found")

    # Get total count
    count_result = (
        supabase.table("answers")
        .select("id", count="exact")
        .eq("author_id", user_id)
        .execute()
    )
    total = count_result.count or 0
    total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

    if page > total_pages:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page} not found. Total pages: {total_pages}"
        )

    # Get answers
    offset = (page - 1) * PAGE_SIZE
    query = (
        supabase.table("answers")
        .select("*, users!answers_author_id_fkey(username)")
        .eq("author_id", user_id)
    )

    if sort == SortOption.top:
        query = query.order("score", desc=True).order("created_at", desc=True)
    else:
        query = query.order("created_at", desc=True)

    result = query.range(offset, offset + PAGE_SIZE - 1).execute()

    answers = [
        AnswerPublic(
            id=a["id"],
            body=a["body"],
            question_id=a["question_id"],
            author_id=a["author_id"],
            author_username=a["users"]["username"],
            status=a["status"],
            upvote_count=a["upvote_count"],
            downvote_count=a["downvote_count"],
            score=a["score"],
            created_at=a["created_at"],
            solana_tx=a.get("solana_tx"),
            solana_tx_url=tx_url(a.get("solana_tx")),
            solana_pda=a.get("solana_pda"),
            solana_pda_url=address_url(a.get("solana_pda")),
        )
        for a in result.data
    ]

    return AnswerListResponse(
        answers=answers,
        page=page,
        total_pages=total_pages,
    )
