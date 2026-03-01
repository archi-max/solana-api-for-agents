import logging
import math
import re

from fastapi import APIRouter, HTTPException, Depends, Query
from app.database import supabase
from app.models.forum import ForumCreateRequest, ForumPublic, ForumListResponse
from app.utils.auth import get_current_user
from app.utils.solana_explorer import tx_url, address_url
from app.solana_client import create_forum as solana_create_forum, keypair_from_json

logger = logging.getLogger(__name__)


def _sanitize_search_word(word: str) -> str:
    """Strip characters significant in PostgREST filter syntax."""
    return re.sub(r'[,.()*%\\]', '', word)


router = APIRouter(prefix="/forums", tags=["forums"])

PAGE_SIZE = 50


def _format_forum(forum: dict) -> ForumPublic:
    """Helper to format forum data with username."""
    return ForumPublic(
        id=forum["id"],
        name=forum["name"],
        description=forum["description"],
        created_by=forum["created_by"],
        created_by_username=forum["users"]["username"],
        question_count=forum["question_count"],
        created_at=forum["created_at"],
        solana_tx=forum.get("solana_tx"),
        solana_tx_url=tx_url(forum.get("solana_tx")),
        solana_pda=forum.get("solana_pda"),
        solana_pda_url=address_url(forum.get("solana_pda")),
    )


@router.get("", response_model=ForumListResponse)
async def list_forums(
    search: str | None = Query(None, description="Search forums by name (space-separated words, all must match)"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
):
    """
    List forums, ranked by activity (question count).

    - Search by name (space-separated keywords, each must appear in name)
    - Returns 50 forums per page

    Public endpoint - no authentication required.
    """
    # Parse search words
    search_words = []
    if search:
        search_words = [_sanitize_search_word(word) for word in search.split() if word.strip()]

    # Get total count
    count_query = supabase.table("forums").select("id", count="exact")
    for word in search_words:
        count_query = count_query.ilike("name", f"%{word}%")

    count_result = count_query.execute()
    total = count_result.count or 0
    total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

    # Out-of-range page returns empty list (not 404)
    if page > total_pages:
        return ForumListResponse(forums=[], page=page, total_pages=total_pages)

    # Get paginated results, ordered by question_count
    offset = (page - 1) * PAGE_SIZE
    query = (
        supabase.table("forums")
        .select("*, users(username)")
    )

    for word in search_words:
        query = query.ilike("name", f"%{word}%")

    result = (
        query
        .order("question_count", desc=True)
        .range(offset, offset + PAGE_SIZE - 1)
        .execute()
    )

    return ForumListResponse(
        forums=[_format_forum(forum) for forum in result.data],
        page=page,
        total_pages=total_pages,
    )


@router.get("/{forum_id}", response_model=ForumPublic)
async def get_forum(forum_id: str):
    """
    Get a specific forum by ID.

    Public endpoint - no authentication required.
    """
    result = supabase.table("forums").select("*, users(username)").eq("id", forum_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Forum not found")

    return _format_forum(result.data[0])


@router.post("", response_model=ForumPublic)
async def create_forum(
    request: ForumCreateRequest,
    user: dict = Depends(get_current_user),
):
    """
    Create a new forum.

    Also creates the forum on Solana (Forum PDA).
    If the Solana transaction fails, the forum is still created in Supabase.

    Any authenticated user can create forums.
    """
    try:
        result = supabase.table("forums").insert({
            "name": request.name,
            "description": request.description,
            "created_by": user["id"],
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create forum")

        forum_data = result.data[0]

        # Try Solana forum creation (non-blocking on failure)
        user_kp = keypair_from_json(user["solana_keypair"]) if user.get("solana_keypair") else None
        solana_result = solana_create_forum(request.name, user_keypair=user_kp)

        if solana_result.signature:
            try:
                supabase.table("forums").update({
                    "solana_tx": solana_result.signature,
                    "solana_pda": solana_result.pda,
                }).eq("id", forum_data["id"]).execute()

                forum_data["solana_tx"] = solana_result.signature
                forum_data["solana_pda"] = solana_result.pda
            except Exception as e:
                logger.warning(f"Failed to update forum with Solana data: {e}")
        else:
            error_msg = solana_result.error or "Unknown Solana error"
            logger.error(f"Solana createForum failed: {error_msg}")
            raise HTTPException(
                status_code=503,
                detail=f"Forum created in database but Solana transaction failed: {error_msg}. The platform wallet may need more SOL.",
            )

        return ForumPublic(
            id=forum_data["id"],
            name=forum_data["name"],
            description=forum_data["description"],
            created_by=forum_data["created_by"],
            created_by_username=user["username"],
            question_count=forum_data["question_count"],
            created_at=forum_data["created_at"],
            solana_tx=forum_data.get("solana_tx"),
            solana_tx_url=tx_url(forum_data.get("solana_tx")),
            solana_pda=forum_data.get("solana_pda"),
            solana_pda_url=address_url(forum_data.get("solana_pda")),
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = str(e)
        if "duplicate key" in error_message or "unique constraint" in error_message.lower():
            raise HTTPException(status_code=409, detail="Forum name already exists. Please choose a different name.")
        raise HTTPException(status_code=500, detail="Failed to create forum")
