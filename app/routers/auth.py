import json
import logging

from fastapi import APIRouter, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from solders.keypair import Keypair
from starlette.requests import Request
from app.database import supabase
from app.models.user import UserRegisterRequest, UserRegisterResponse, UserPublic
from app.utils.api_key import generate_api_key
from app.utils.solana_explorer import address_url
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
    # Generate API key and Solana keypair
    full_api_key, prefix, hashed_key = generate_api_key()
    user_keypair = Keypair()
    wallet_address = str(user_keypair.pubkey())
    keypair_bytes_json = json.dumps(list(bytes(user_keypair)))

    # Insert user into database
    try:
        result = supabase.table("users").insert({
            "username": body.username,
            "api_key_prefix": prefix,
            "api_key_hash": hashed_key,
            "wallet_address": wallet_address,
            "solana_keypair": keypair_bytes_json,
        }).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create user")

        user_data = result.data[0]

        # Try Solana registration (non-blocking on failure)
        solana_result = solana_register_user(
            wallet_address=wallet_address,
            username=body.username,
            user_keypair=user_keypair,
        )

        # Update Supabase with Solana PDA if successful
        if solana_result.signature:
            try:
                supabase.table("users").update({
                    "solana_profile_pda": solana_result.pda,
                }).eq("id", user_data["id"]).execute()

                user_data["solana_profile_pda"] = solana_result.pda
            except Exception as e:
                logger.warning(f"Failed to update user with Solana data: {e}")
        else:
            error_msg = solana_result.error or "Unknown Solana error"
            logger.error(f"Solana registration failed: {error_msg}")
            raise HTTPException(
                status_code=503,
                detail=f"User created in database but Solana transaction failed: {error_msg}. The platform wallet may need more SOL.",
            )

        return UserRegisterResponse(
            user=UserPublic(
                id=user_data["id"],
                username=user_data["username"],
                question_count=user_data.get("question_count", 0),
                answer_count=user_data.get("answer_count", 0),
                reputation=user_data.get("reputation", 0),
                created_at=user_data["created_at"],
                wallet_address=wallet_address,
                solana_pda=user_data.get("solana_profile_pda"),
                solana_pda_url=address_url(user_data.get("solana_profile_pda")),
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
