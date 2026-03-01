from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.database import supabase
from app.utils.api_key import extract_prefix, verify_api_key

security = HTTPBearer()
optional_auth = HTTPBearer(auto_error=False)

_USER_COLUMNS = "id, username, api_key_hash, question_count, answer_count, reputation, created_at, is_admin, wallet_address, solana_keypair, solana_profile_pda"


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Dependency that validates API key and returns the current user.

    Usage in endpoints:
        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"message": f"Hello {user['username']}"}
    """
    api_key = credentials.credentials

    # Extract prefix from API key
    prefix = extract_prefix(api_key)
    if not prefix:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format",
        )

    # Look up user by prefix
    result = supabase.table("users").select(_USER_COLUMNS).eq("api_key_prefix", prefix).execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    user = result.data[0]

    # Verify the full API key against the hash
    if not verify_api_key(api_key, user["api_key_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_auth),
) -> dict | None:
    """Get user if authenticated, None otherwise. Does not error on missing/invalid auth."""
    if not credentials:
        return None

    api_key = credentials.credentials
    prefix = extract_prefix(api_key)
    if not prefix:
        return None

    result = supabase.table("users").select(_USER_COLUMNS).eq("api_key_prefix", prefix).execute()
    if not result.data:
        return None

    user = result.data[0]
    if not verify_api_key(api_key, user["api_key_hash"]):
        return None

    return user
