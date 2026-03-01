import secrets
import bcrypt


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        tuple: (full_api_key, prefix, hashed_key)
        - full_api_key: The complete key to give to the user (shown once!)
        - prefix: The first part stored in plaintext for lookups (e.g., "co_a1b2c3d4")
        - hashed_key: Bcrypt hash of the full key for verification
    """
    # Generate random parts
    prefix_random = secrets.token_hex(4)  # 8 hex chars
    secret_part = secrets.token_urlsafe(32)  # ~43 chars of randomness

    # Construct the full key and prefix
    prefix = f"co_{prefix_random}"
    full_api_key = f"{prefix}_{secret_part}"

    # Hash the full key
    hashed_key = bcrypt.hashpw(full_api_key.encode(), bcrypt.gensalt()).decode()

    return full_api_key, prefix, hashed_key


def verify_api_key(full_api_key: str, hashed_key: str) -> bool:
    """
    Verify an API key against its hash.

    Args:
        full_api_key: The API key provided by the user
        hashed_key: The stored bcrypt hash

    Returns:
        bool: True if the key is valid
    """
    return bcrypt.checkpw(full_api_key.encode(), hashed_key.encode())


def extract_prefix(full_api_key: str) -> str | None:
    """
    Extract the prefix from a full API key.

    Args:
        full_api_key: e.g., "co_a1b2c3d4_xxxxxxxxxxx"

    Returns:
        The prefix (e.g., "co_a1b2c3d4") or None if invalid format
    """
    parts = full_api_key.split("_")
    if len(parts) >= 3 and parts[0] == "co":
        return f"{parts[0]}_{parts[1]}"
    return None
