"""
Migration script: Generate per-user Solana keypairs for existing users.

Run this AFTER adding the `solana_keypair` column to the users table:
    ALTER TABLE public.users ADD COLUMN solana_keypair TEXT;

Usage:
    python3 scripts/migrate_keypairs.py
"""

import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solders.keypair import Keypair
from supabase import create_client
from app.config import settings

def main():
    supabase = create_client(settings.supabase_url, settings.supabase_service_key)

    # Find users without a keypair
    result = supabase.table("users").select("id, username, wallet_address").is_("solana_keypair", "null").execute()

    if not result.data:
        print("No users need migration — all have keypairs.")
        return

    print(f"Found {len(result.data)} users without keypairs. Migrating...")

    for user in result.data:
        kp = Keypair()
        wallet = str(kp.pubkey())
        kp_json = json.dumps(list(bytes(kp)))

        supabase.table("users").update({
            "wallet_address": wallet,
            "solana_keypair": kp_json,
        }).eq("id", user["id"]).execute()

        print(f"  {user['username']}: {wallet}")

    print(f"\nDone. Migrated {len(result.data)} users.")
    print("Note: Existing on-chain data (PDAs) was created under the old platform wallet.")
    print("New content from these users will use their individual wallets.")


if __name__ == "__main__":
    main()
