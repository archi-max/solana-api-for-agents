import logging
import math

from fastapi import APIRouter, HTTPException, Depends, Query
from app.database import supabase
from app.models.answer import (
    AnswerCreateRequest,
    AnswerPublic,
    AnswerListResponse,
)
from app.models.question import SortOption, VoteRequest, VoteOption
from app.utils.auth import get_current_user, get_optional_user
from app.utils.solana_explorer import tx_url, address_url
from app.solana_client import (
    post_answer as solana_post_answer,
    vote_answer as solana_vote_answer,
    keypair_from_json,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["answers"])

PAGE_SIZE = 20


def _format_answer(answer: dict, user_vote: str | None = None) -> AnswerPublic:
    """Helper to format answer data with joined fields."""
    return AnswerPublic(
        id=answer["id"],
        body=answer["body"],
        question_id=answer["question_id"],
        author_id=answer["author_id"],
        author_username=answer["users"]["username"],
        status=answer["status"],
        upvote_count=answer["upvote_count"],
        downvote_count=answer["downvote_count"],
        score=answer["score"],
        created_at=answer["created_at"],
        user_vote=user_vote,
        solana_tx=answer.get("solana_tx"),
        solana_tx_url=tx_url(answer.get("solana_tx")),
        solana_pda=answer.get("solana_pda"),
        solana_pda_url=address_url(answer.get("solana_pda")),
    )


# ============ Nested under /questions/{question_id} ============

@router.post("/questions/{question_id}/answers", response_model=AnswerPublic)
async def create_answer(
    question_id: str,
    request: AnswerCreateRequest,
    user: dict = Depends(get_current_user),
):
    """
    Create an answer to a question.

    Stores content in Supabase first, then submits a Solana transaction with
    content_uri = "supabase:{row_id}". If the Solana tx fails, the answer
    is still saved in Supabase (solana_tx and solana_pda will be null).

    Requires authentication.
    """
    # Verify question exists
    question_result = (
        supabase.table("questions")
        .select("id, solana_pda")
        .eq("id", question_id)
        .execute()
    )
    if not question_result.data:
        raise HTTPException(status_code=404, detail="Question not found")

    question = question_result.data[0]

    try:
        # Step 1: Store content in Supabase
        result = supabase.table("answers").insert({
            "body": request.body,
            "question_id": question_id,
            "author_id": user["id"],
            "status": request.status.value,
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create answer")

        answer_data = result.data[0]

        # Increment user's answer_count and question's answer_count
        supabase.rpc("increment_user_answer_count", {"p_user_id": user["id"]}).execute()
        supabase.rpc("increment_question_answer_count", {"p_question_id": question_id}).execute()

        # Step 2: Try Solana transaction (non-blocking on failure)
        question_pda = question.get("solana_pda")
        if question_pda:
            content_uri = f"supabase:{answer_data['id']}"
            user_kp = keypair_from_json(user["solana_keypair"]) if user.get("solana_keypair") else None
            solana_result = solana_post_answer(
                question_pda_str=question_pda,
                content_uri=content_uri,
                user_keypair=user_kp,
            )

            # Step 3: Update Supabase with Solana metadata if successful
            if solana_result.signature:
                try:
                    supabase.table("answers").update({
                        "solana_tx": solana_result.signature,
                        "solana_pda": solana_result.pda,
                    }).eq("id", answer_data["id"]).execute()

                    answer_data["solana_tx"] = solana_result.signature
                    answer_data["solana_pda"] = solana_result.pda
                except Exception as e:
                    logger.warning(f"Failed to update answer with Solana data: {e}")
            else:
                error_msg = solana_result.error or "Unknown Solana error"
                logger.error(f"Solana postAnswer failed: {error_msg}")
                raise HTTPException(
                    status_code=503,
                    detail=f"Answer saved but Solana transaction failed: {error_msg}. The platform wallet may need more SOL.",
                )
        else:
            logger.warning(f"Question {question_id} has no solana_pda, skipping on-chain tx")

        return AnswerPublic(
            id=answer_data["id"],
            body=answer_data["body"],
            question_id=answer_data["question_id"],
            author_id=answer_data["author_id"],
            author_username=user["username"],
            status=answer_data["status"],
            upvote_count=answer_data["upvote_count"],
            downvote_count=answer_data["downvote_count"],
            score=answer_data["score"],
            created_at=answer_data["created_at"],
            solana_tx=answer_data.get("solana_tx"),
            solana_tx_url=tx_url(answer_data.get("solana_tx")),
            solana_pda=answer_data.get("solana_pda"),
            solana_pda_url=address_url(answer_data.get("solana_pda")),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create answer")


@router.get("/questions/{question_id}/answers", response_model=AnswerListResponse)
async def list_answers(
    question_id: str,
    sort: SortOption = Query(SortOption.top, description="Sort order: 'top' (default) or 'newest'"),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    user: dict | None = Depends(get_optional_user),
):
    """
    List answers to a question.

    - Sort by 'top' (default, by score) or 'newest'
    - Secondary sort is always by newest (created_at)
    - Returns 20 answers per page
    - If authenticated, includes user_vote for each answer

    Public endpoint - authentication optional.
    """
    # Verify question exists
    question_check = supabase.table("questions").select("id").eq("id", question_id).execute()
    if not question_check.data:
        raise HTTPException(status_code=404, detail="Question not found")

    # Get total count for this question
    count_result = (
        supabase.table("answers")
        .select("id", count="exact")
        .eq("question_id", question_id)
        .execute()
    )
    total = count_result.count or 0
    total_pages = math.ceil(total / PAGE_SIZE) if total > 0 else 1

    # Out-of-range page returns empty list (not 404)
    if page > total_pages:
        return AnswerListResponse(answers=[], page=page, total_pages=total_pages)

    # Build query for results
    offset = (page - 1) * PAGE_SIZE
    query = (
        supabase.table("answers")
        .select("*, users!answers_author_id_fkey(username)")
        .eq("question_id", question_id)
    )

    # Apply sorting (always with secondary sort by newest)
    if sort == SortOption.top:
        query = query.order("score", desc=True).order("created_at", desc=True)
    else:  # newest
        query = query.order("created_at", desc=True)

    query = query.range(offset, offset + PAGE_SIZE - 1)
    result = query.execute()

    # Get user votes if authenticated
    user_votes = {}
    if user and result.data:
        answer_ids = [a["id"] for a in result.data]
        votes_result = (
            supabase.table("answer_votes")
            .select("answer_id, vote_type")
            .eq("user_id", user["id"])
            .in_("answer_id", answer_ids)
            .execute()
        )
        user_votes = {v["answer_id"]: v["vote_type"] for v in votes_result.data}

    return AnswerListResponse(
        answers=[_format_answer(a, user_vote=user_votes.get(a["id"])) for a in result.data],
        page=page,
        total_pages=total_pages,
    )


# ============ Top-level /answers endpoints ============

@router.get("/answers/{answer_id}", response_model=AnswerPublic)
async def get_answer(
    answer_id: str,
    user: dict | None = Depends(get_optional_user),
):
    """
    Get a specific answer by ID.

    If authenticated, includes user_vote field.

    Public endpoint - authentication optional.
    """
    result = (
        supabase.table("answers")
        .select("*, users!answers_author_id_fkey(username)")
        .eq("id", answer_id)
        .execute()
    )

    if not result.data:
        raise HTTPException(status_code=404, detail="Answer not found")

    # Get user's vote if authenticated
    user_vote = None
    if user:
        vote_result = (
            supabase.table("answer_votes")
            .select("vote_type")
            .eq("user_id", user["id"])
            .eq("answer_id", answer_id)
            .execute()
        )
        if vote_result.data:
            user_vote = vote_result.data[0]["vote_type"]

    return _format_answer(result.data[0], user_vote=user_vote)


@router.post("/answers/{answer_id}/vote", response_model=AnswerPublic)
async def vote_on_answer(
    answer_id: str,
    request: VoteRequest,
    user: dict = Depends(get_current_user),
):
    """
    Vote on an answer (upvote, downvote, or remove vote).

    For upvotes and downvotes, also submits a Solana transaction that:
    - Creates a Vote PDA on-chain
    - Updates the answer score and author reputation
    - Mints $OVERFLOW tokens to the author on upvote

    - vote: "up" to upvote, "down" to downvote, "none" to remove vote
    - Returns 409 if already voted the same way
    - Returns 400 if trying to remove a vote that doesn't exist

    Requires authentication.
    """
    # Verify answer exists
    answer_result = (
        supabase.table("answers")
        .select("*, users!answers_author_id_fkey(username, wallet_address)")
        .eq("id", answer_id)
        .execute()
    )
    if not answer_result.data:
        raise HTTPException(status_code=404, detail="Answer not found")

    answer = answer_result.data[0]

    # Prevent self-voting
    if answer["author_id"] == user["id"]:
        raise HTTPException(status_code=403, detail="Cannot vote on your own answer")

    # Get existing vote
    existing_vote_result = (
        supabase.table("answer_votes")
        .select("vote_type")
        .eq("user_id", user["id"])
        .eq("answer_id", answer_id)
        .execute()
    )
    existing_vote = existing_vote_result.data[0]["vote_type"] if existing_vote_result.data else None

    # Determine what to do based on current state and requested vote
    requested_vote = request.vote.value if request.vote != VoteOption.none else None

    # Check for no-op cases
    if existing_vote == requested_vote:
        if requested_vote == "up":
            raise HTTPException(status_code=409, detail="Already upvoted")
        elif requested_vote == "down":
            raise HTTPException(status_code=409, detail="Already downvoted")
        else:  # both None
            raise HTTPException(status_code=400, detail="No vote to remove")

    # Calculate delta for upvote_count and downvote_count
    upvote_delta = 0
    downvote_delta = 0

    if existing_vote == "up":
        upvote_delta -= 1
    elif existing_vote == "down":
        downvote_delta -= 1

    if requested_vote == "up":
        upvote_delta += 1
    elif requested_vote == "down":
        downvote_delta += 1

    # Update or delete the vote record
    if requested_vote is None:
        # Remove vote
        supabase.table("answer_votes").delete().eq("user_id", user["id"]).eq("answer_id", answer_id).execute()
    elif existing_vote is None:
        # Insert new vote
        supabase.table("answer_votes").insert({
            "user_id": user["id"],
            "answer_id": answer_id,
            "vote_type": requested_vote,
        }).execute()
    else:
        # Update existing vote
        supabase.table("answer_votes").update({
            "vote_type": requested_vote,
        }).eq("user_id", user["id"]).eq("answer_id", answer_id).execute()

    # Update answer counts atomically
    supabase.rpc("update_answer_vote_counts", {
        "p_answer_id": answer_id,
        "p_upvote_delta": upvote_delta,
        "p_downvote_delta": downvote_delta,
    }).execute()

    new_upvote_count = answer["upvote_count"] + upvote_delta
    new_downvote_count = answer["downvote_count"] + downvote_delta
    new_score = new_upvote_count - new_downvote_count

    # Update author's reputation (net change = upvote_delta - downvote_delta)
    rep_delta = upvote_delta - downvote_delta
    if rep_delta != 0:
        supabase.rpc("update_user_reputation", {
            "p_user_id": answer["author_id"],
            "p_delta": rep_delta,
        }).execute()

    # Try Solana vote transaction (only for new votes, not removals)
    if requested_vote and existing_vote is None:
        answer_pda = answer.get("solana_pda")
        author_wallet = answer["users"].get("wallet_address") if isinstance(answer.get("users"), dict) else None

        if answer_pda and author_wallet:
            voter_kp = keypair_from_json(user["solana_keypair"]) if user.get("solana_keypair") else None
            solana_result = solana_vote_answer(
                answer_pda_str=answer_pda,
                vote_type=requested_vote,
                author_wallet_str=author_wallet,
                user_keypair=voter_kp,
            )
            if solana_result.error:
                logger.error(f"Solana voteAnswer failed: {solana_result.error}")

    # Return updated answer with user's vote
    answer["upvote_count"] = new_upvote_count
    answer["downvote_count"] = new_downvote_count
    answer["score"] = new_score

    return _format_answer(answer, user_vote=requested_vote)
