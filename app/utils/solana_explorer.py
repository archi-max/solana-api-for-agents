"""Helpers for building Solana Devnet explorer URLs."""

EXPLORER_BASE = "https://explorer.solana.com"


def tx_url(signature: str | None) -> str | None:
    """Return a Solana Explorer link for a transaction signature, or None."""
    if not signature:
        return None
    return f"{EXPLORER_BASE}/tx/{signature}?cluster=devnet"


def address_url(address: str | None) -> str | None:
    """Return a Solana Explorer link for an account/PDA address, or None."""
    if not address:
        return None
    return f"{EXPLORER_BASE}/address/{address}?cluster=devnet"
