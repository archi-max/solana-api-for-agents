import logging

from fastapi import APIRouter, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
from app.database import supabase
from app.models.user import UserRegisterRequest, UserRegisterResponse, UserPublic
from app.utils.api_key import generate_api_key
from app.solana_client import register_user as solana_register_user

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRegisterResponse)
@limiter.limit("5/minute")
async def register(request: Request, body: UserRegisterRequest):
    """
    Register a new user and receive an API key.

    Also registers the user on Solana (creates UserProfile PDA + $OVERFLOW token account).
    If the Solana transaction fails, registration still succeeds (Supabase content is preserved).

    The API key is only shown once - store it securely!
    """
    # Generate API key
    full_api_key, prefix, hashed_key = generate_api_key()

    # Insert user into database
    try:
        result = supabase.table("users").insert({
            "username": body.username,
            "api_key_prefix": prefix,
            "api_key_hash": hashed_key,
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create user")

        user_data = result.data[0]

        # Try Solana registration (non-blocking on failure)
        solana_result = solana_register_user(
            wallet_address=user_data["id"],  # Using user ID as reference
            username=body.username,
        )

        # Update Supabase with Solana data if successful
        if solana_result.signature:
            try:
                supabase.table("users").update({
                    "wallet_address": solana_result.pda,  # Store the user profile PDA
                    "solana_tx": solana_result.signature,
                    "solana_pda": solana_result.pda,
                }).eq("id", user_data["id"]).execute()

                user_data["wallet_address"] = solana_result.pda
                user_data["solana_pda"] = solana_result.pda
            except Exception as e:
                logger.warning(f"Failed to update user with Solana data: {e}")
        else:
            if solana_result.error:
                logger.warning(f"Solana registration failed (non-fatal): {solana_result.error}")

        return UserRegisterResponse(
            user=UserPublic(
                id=user_data["id"],
                username=user_data["username"],
                question_count=user_data.get("question_count", 0),
                answer_count=user_data.get("answer_count", 0),
                reputation=user_data.get("reputation", 0),
                created_at=user_data["created_at"],
                wallet_address=user_data.get("wallet_address"),
                solana_pda=user_data.get("solana_pda"),
            ),
            api_key=full_api_key,
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = str(e)
        # Check for unique constraint violation (username already taken)
        if "duplicate key" in error_message or "unique constraint" in error_message.lower():
            raise HTTPException(status_code=409, detail="Username already taken. Please try a different username.")
        raise HTTPException(status_code=500, detail="Registration failed")
