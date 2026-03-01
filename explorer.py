"""
ChatOverflow Solana Explorer
=============================
Walks through ALL on-chain program accounts and shows exactly what's stored,
how SOL flows, how $OVERFLOW tokens get minted, and verifies integrity.

Run: python3 explorer.py
"""

import json
import hashlib
import struct
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from solders.pubkey import Pubkey
from solana.rpc.api import Client as SolanaClient
from solana.rpc.commitment import Confirmed

from app.config import settings
from app.solana_client import (
    find_platform_pda, find_reward_mint_pda, find_forum_pda,
    find_user_profile_pda, find_question_pda, find_answer_pda, find_vote_pda,
    get_associated_token_address, REWARD_MINT, TOKEN_PROGRAM_ID,
)

rpc = SolanaClient(settings.solana_rpc_url)
PROGRAM_ID = Pubkey.from_string(settings.program_id)

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

def sol(lamports):
    """Convert lamports to SOL display."""
    return f"{lamports / 1e9:.4f} SOL"

def tokens(raw, decimals=6):
    """Convert raw token units to display."""
    return f"{raw / (10 ** decimals):,.{decimals}f}"

def read_account(pubkey):
    """Read raw account data from chain."""
    resp = rpc.get_account_info(pubkey, commitment=Confirmed)
    if not resp.value:
        return None
    return resp.value

def read_string(data, offset):
    """Read an Anchor string (4-byte LE length prefix + UTF-8)."""
    length = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    s = data[offset:offset + length].decode("utf-8", errors="replace")
    return s, offset + length

def read_pubkey(data, offset):
    """Read a 32-byte pubkey."""
    pk = Pubkey.from_bytes(data[offset:offset + 32])
    return pk, offset + 32

def section(title):
    print(f"\n{'=' * 70}")
    print(f"{BOLD}{CYAN}{title}{RESET}")
    print(f"{'=' * 70}")

def field(name, value, indent=2):
    print(f"{' ' * indent}{DIM}{name}:{RESET} {value}")

def good(msg):
    print(f"  {GREEN}✓ {msg}{RESET}")

def warn(msg):
    print(f"  {YELLOW}⚠ {msg}{RESET}")

def bad(msg):
    print(f"  {RED}✗ {msg}{RESET}")

# =========================================================================
# 1. PLATFORM ACCOUNT
# =========================================================================
section("1. PLATFORM (singleton — program config)")

platform_pda, platform_bump = find_platform_pda()
platform_acct = read_account(platform_pda)

if not platform_acct:
    bad("Platform not initialized!")
    sys.exit(1)

d = bytes(platform_acct.data)
off = 8  # skip discriminator
authority, off = read_pubkey(d, off)
reward_mint, off = read_pubkey(d, off)
reward_per_upvote = struct.unpack_from("<Q", d, off)[0]; off += 8
reward_per_accepted = struct.unpack_from("<Q", d, off)[0]; off += 8
bump = d[off]; off += 1

field("Address", f"{BOLD}{platform_pda}{RESET}")
field("Owner", f"{PROGRAM_ID} (our program)")
field("Authority (admin)", authority)
field("Reward Mint", reward_mint)
field("Reward per upvote", f"{BOLD}{tokens(reward_per_upvote)} $OVERFLOW{RESET} ({reward_per_upvote:,} raw units)")
field("Reward per accepted", f"{tokens(reward_per_accepted)} $OVERFLOW ({reward_per_accepted:,} raw units)")
field("PDA bump", bump)
field("Rent (SOL locked)", sol(platform_acct.lamports))

print(f"\n  {DIM}How this works:{RESET}")
print(f"  The Platform PDA is the mint authority for $OVERFLOW.")
print(f"  When someone upvotes, the program signs a mint_to CPI as this PDA.")
print(f"  No human wallet can mint — only the program via PDA seeds ['platform'].")

# =========================================================================
# 2. REWARD MINT ($OVERFLOW TOKEN)
# =========================================================================
section("2. REWARD MINT ($OVERFLOW token)")

reward_mint_pda, _ = find_reward_mint_pda()
mint_acct = read_account(reward_mint_pda)

if mint_acct:
    md = bytes(mint_acct.data)
    # SPL Token Mint layout: 4 (coption) + 32 (authority) + 8 (supply) + 1 (decimals) + 1 (initialized) + 4+32 (freeze auth)
    mint_authority_option = struct.unpack_from("<I", md, 0)[0]
    mint_authority_pk = Pubkey.from_bytes(md[4:36])
    supply = struct.unpack_from("<Q", md, 36)[0]
    decimals = md[44]

    field("Address", f"{BOLD}{reward_mint_pda}{RESET}")
    field("Owner", f"{TOKEN_PROGRAM_ID} (SPL Token Program)")
    field("Mint Authority", f"{mint_authority_pk}")
    if str(mint_authority_pk) == str(platform_pda):
        good("Mint authority = Platform PDA (program-controlled, no human can mint)")
    else:
        bad(f"Mint authority mismatch! Expected {platform_pda}")
    field("Total Supply", f"{BOLD}{tokens(supply, decimals)} $OVERFLOW{RESET} ({supply:,} raw)")
    field("Decimals", decimals)
    field("Rent (SOL locked)", sol(mint_acct.lamports))

    print(f"\n  {DIM}Token flow:{RESET}")
    print(f"  1. User A upvotes User B's question/answer")
    print(f"  2. Program creates Vote PDA → prevents double voting")
    print(f"  3. Program does CPI: mint_to(amount={reward_per_upvote:,}, to=B's ATA)")
    print(f"  4. Platform PDA signs the CPI (seeds=['platform', bump={bump}])")
    print(f"  5. {tokens(reward_per_upvote)} $OVERFLOW appears in B's token account")
    print(f"  6. Total supply increases by {reward_per_upvote:,}")
else:
    bad("Reward mint not found!")

# =========================================================================
# 3. FORUMS
# =========================================================================
section("3. FORUMS (on-chain)")

# Scan for known forums + any from Supabase
known_forums = ["general", "Solana Development", "Solana Agents", "E2E Test Forum", "CI Test Forum"]

# Also get all program accounts of type Forum
print(f"\n  Scanning all Forum accounts owned by {PROGRAM_ID}...")
all_accounts = rpc.get_program_accounts(PROGRAM_ID, commitment=Confirmed)
forums_found = []
questions_found = []
answers_found = []
votes_found = []
profiles_found = []

if all_accounts.value:
    for acct in all_accounts.value:
        data = bytes(acct.account.data)
        if len(data) < 8:
            continue
        disc = data[:8]

        # We identify account types by discriminator
        # Let's compute expected discriminators
        # Anchor discriminator = first 8 bytes of SHA-256("account:<AccountName>")
        forum_disc = hashlib.sha256(b"account:Forum").digest()[:8]
        question_disc = hashlib.sha256(b"account:Question").digest()[:8]
        answer_disc = hashlib.sha256(b"account:Answer").digest()[:8]
        vote_disc = hashlib.sha256(b"account:Vote").digest()[:8]
        profile_disc = hashlib.sha256(b"account:UserProfile").digest()[:8]
        platform_disc = hashlib.sha256(b"account:Platform").digest()[:8]

        if disc == forum_disc:
            forums_found.append((acct.pubkey, data, acct.account.lamports))
        elif disc == question_disc:
            questions_found.append((acct.pubkey, data, acct.account.lamports))
        elif disc == answer_disc:
            answers_found.append((acct.pubkey, data, acct.account.lamports))
        elif disc == vote_disc:
            votes_found.append((acct.pubkey, data, acct.account.lamports))
        elif disc == profile_disc:
            profiles_found.append((acct.pubkey, data, acct.account.lamports))

print(f"  Found: {len(forums_found)} forums, {len(questions_found)} questions, {len(answers_found)} answers, {len(votes_found)} votes, {len(profiles_found)} profiles")

for pk, data, lamports in forums_found:
    off = 8
    auth, off = read_pubkey(data, off)
    name, off = read_string(data, off)
    q_count = struct.unpack_from("<Q", data, off)[0]; off += 8
    created_at = struct.unpack_from("<q", data, off)[0]; off += 8
    bump = data[off]

    print(f"\n  {BOLD}Forum: \"{name}\"{RESET}")
    field("PDA", pk, 4)
    field("Authority", auth, 4)
    field("Question count", q_count, 4)
    field("Rent locked", sol(lamports), 4)

# =========================================================================
# 4. USER PROFILES
# =========================================================================
section("4. USER PROFILES (on-chain)")

for pk, data, lamports in profiles_found:
    off = 8
    auth, off = read_pubkey(data, off)
    username, off = read_string(data, off)
    reputation = struct.unpack_from("<q", data, off)[0]; off += 8
    q_posted = struct.unpack_from("<I", data, off)[0]; off += 4
    a_posted = struct.unpack_from("<I", data, off)[0]; off += 4
    created_at = struct.unpack_from("<q", data, off)[0]; off += 8

    print(f"\n  {BOLD}User: \"{username}\"{RESET}")
    field("PDA", pk, 4)
    field("Wallet", auth, 4)
    field("Reputation", f"{BOLD}{reputation}{RESET}", 4)
    field("Questions posted", q_posted, 4)
    field("Answers posted", a_posted, 4)
    field("Rent locked", sol(lamports), 4)

    # Check token balance
    ata = get_associated_token_address(auth, REWARD_MINT)
    ata_acct = read_account(ata)
    if ata_acct:
        ata_data = bytes(ata_acct.data)
        # SPL Token Account layout: 32 (mint) + 32 (owner) + 8 (amount) ...
        balance = struct.unpack_from("<Q", ata_data, 64)[0]
        field("$OVERFLOW balance", f"{BOLD}{GREEN}{tokens(balance)} $OVERFLOW{RESET} ({balance:,} raw)", 4)
    else:
        field("$OVERFLOW balance", "0 (no ATA)", 4)

# =========================================================================
# 5. QUESTIONS
# =========================================================================
section("5. QUESTIONS (on-chain)")

for pk, data, lamports in questions_found:
    off = 8
    author, off = read_pubkey(data, off)
    forum, off = read_pubkey(data, off)
    question_id = struct.unpack_from("<Q", data, off)[0]; off += 8
    title_hash = data[off:off + 32]; off += 32
    content_uri, off = read_string(data, off)
    score = struct.unpack_from("<q", data, off)[0]; off += 8
    answer_count = struct.unpack_from("<I", data, off)[0]; off += 4
    created_at = struct.unpack_from("<q", data, off)[0]; off += 8

    print(f"\n  {BOLD}Question #{question_id}{RESET}")
    field("PDA", pk, 4)
    field("Author wallet", author, 4)
    field("Forum PDA", forum, 4)
    field("Content URI", f"{CYAN}{content_uri}{RESET}", 4)
    field("Title hash (on-chain)", title_hash.hex(), 4)
    field("Score", f"{BOLD}{score}{RESET}", 4)
    field("Answer count", answer_count, 4)
    field("Rent locked", sol(lamports), 4)

    # INTEGRITY CHECK: if content_uri starts with "supabase:", fetch and verify
    if content_uri.startswith("supabase:"):
        supabase_id = content_uri.replace("supabase:", "")
        print(f"\n    {BOLD}--- Integrity Verification ---{RESET}")
        try:
            from app.database import supabase
            q_result = supabase.table("questions").select("title, body").eq("id", supabase_id).execute()
            if q_result.data:
                db_title = q_result.data[0]["title"]
                expected_hash = hashlib.sha256(db_title.encode("utf-8")).digest()
                field("Supabase title", f'"{db_title}"', 4)
                field("Expected hash", expected_hash.hex(), 4)
                if expected_hash == title_hash:
                    good("  INTEGRITY MATCH: On-chain title_hash matches SHA-256 of Supabase title")
                else:
                    bad("  INTEGRITY MISMATCH: Title was tampered with in Supabase!")
            else:
                warn(f"  Supabase row {supabase_id} not found (may have been cleaned up)")
        except Exception as e:
            warn(f"  Could not verify (Supabase error): {e}")

# =========================================================================
# 6. ANSWERS
# =========================================================================
section("6. ANSWERS (on-chain)")

for pk, data, lamports in answers_found:
    off = 8
    author, off = read_pubkey(data, off)
    question, off = read_pubkey(data, off)
    answer_id = struct.unpack_from("<I", data, off)[0]; off += 4
    content_uri, off = read_string(data, off)
    score = struct.unpack_from("<q", data, off)[0]; off += 8
    is_accepted = bool(data[off]); off += 1
    created_at = struct.unpack_from("<q", data, off)[0]; off += 8

    print(f"\n  {BOLD}Answer #{answer_id}{RESET}")
    field("PDA", pk, 4)
    field("Author wallet", author, 4)
    field("Question PDA", question, 4)
    field("Content URI", f"{CYAN}{content_uri}{RESET}", 4)
    field("Score", f"{BOLD}{score}{RESET}", 4)
    field("Accepted", is_accepted, 4)
    field("Rent locked", sol(lamports), 4)

# =========================================================================
# 7. VOTES
# =========================================================================
section("7. VOTES (on-chain)")

for pk, data, lamports in votes_found:
    off = 8
    voter, off = read_pubkey(data, off)
    target, off = read_pubkey(data, off)
    vote_type_idx = data[off]; off += 1
    vote_type = "UP" if vote_type_idx == 0 else "DOWN"
    created_at = struct.unpack_from("<q", data, off)[0]; off += 8

    color = GREEN if vote_type == "UP" else RED
    print(f"\n  {color}{BOLD}{vote_type}{RESET} vote")
    field("PDA", pk, 4)
    field("Voter", voter, 4)
    field("Target", target, 4)
    field("Rent locked", sol(lamports), 4)

# =========================================================================
# 8. SOL FLOW SUMMARY
# =========================================================================
section("8. SOL FLOW SUMMARY")

total_rent = 0
if platform_acct:
    total_rent += platform_acct.lamports
if mint_acct:
    total_rent += mint_acct.lamports
for _, _, l in forums_found:
    total_rent += l
for _, _, l in questions_found:
    total_rent += l
for _, _, l in answers_found:
    total_rent += l
for _, _, l in votes_found:
    total_rent += l
for _, _, l in profiles_found:
    total_rent += l

print(f"""
  {BOLD}Where does SOL go?{RESET}

  Every on-chain account requires rent (SOL locked as deposit).
  When you create a forum, question, answer, vote, or profile,
  the signer pays rent from their wallet.

  {BOLD}Current rent locked in program accounts:{RESET}

    Platform:    1 account  = rent for config data
    Mint:        1 account  = rent for token mint
    Forums:      {len(forums_found)} accounts
    Questions:   {len(questions_found)} accounts
    Answers:     {len(answers_found)} accounts
    Votes:       {len(votes_found)} accounts
    Profiles:    {len(profiles_found)} accounts
    ─────────────────────────
    Total:       {BOLD}{sol(total_rent)}{RESET} locked as rent

  {BOLD}SOL is NOT transferred between users.{RESET}
  SOL only flows: User wallet → Solana runtime (rent deposit)
  Rent is recoverable if accounts are closed.
""")

# =========================================================================
# 9. TOKEN FLOW SUMMARY
# =========================================================================
section("9. $OVERFLOW TOKEN FLOW")

if mint_acct:
    print(f"""
  {BOLD}How $OVERFLOW tokens work:{RESET}

  ┌─────────┐    upvote    ┌─────────────┐    CPI: mint_to    ┌──────────────┐
  │ Voter   │ ──────────→  │ Program     │ ─────────────────→  │ Author's ATA │
  │ (signs) │              │ (validates) │                     │ (gets tokens)│
  └─────────┘              └──────┬──────┘                     └──────────────┘
                                  │
                           signs as Platform PDA
                           seeds=["platform"]

  Per upvote:  {BOLD}{tokens(reward_per_upvote)} $OVERFLOW{RESET}
  Total minted so far: {BOLD}{tokens(supply, decimals)} $OVERFLOW{RESET}
  Total upvotes that occurred: ~{supply // reward_per_upvote if reward_per_upvote > 0 else 0}

  {BOLD}No SOL is exchanged for tokens.{RESET}
  Tokens are minted from nothing by the program.
  The mint authority is the Platform PDA — no human can mint.

  {BOLD}Can we change tokens per upvote?{RESET}
  {RED}Currently NO{RESET} — reward_per_upvote is set once during initializePlatform.
  To change it, we need an updatePlatform instruction in the Solana program.

  {BOLD}Can we exchange $OVERFLOW for SOL?{RESET}
  {RED}Currently NO{RESET} — $OVERFLOW has no built-in exchange mechanism.
  Options to add:
    1. Raydium/Orca liquidity pool ($OVERFLOW ↔ SOL) — market sets price
    2. Program treasury: lock SOL, exchange at fixed rate (e.g. 100 $OVERFLOW = 0.01 SOL)
    3. Bonding curve: price rises with supply (DeFi-native approach)
""")

# =========================================================================
# 10. INTEGRITY VERIFICATION GUIDE
# =========================================================================
section("10. INTEGRITY VERIFICATION")

print(f"""
  {BOLD}How content integrity works:{RESET}

  When a question is posted:
    1. API computes SHA-256(title) → 32 bytes
    2. On-chain Question account stores this as title_hash
    3. On-chain also stores content_uri = "supabase:<uuid>"

  To verify integrity:
    1. Read the Question PDA from Solana
    2. Fetch the title from Supabase using the UUID in content_uri
    3. Compute SHA-256(supabase_title)
    4. Compare with on-chain title_hash

  If they match → content hasn't been tampered with
  If they don't → someone modified the Supabase title after posting

  {BOLD}What's tamper-proof (on-chain):{RESET}
    ✓ Scores (can only change via vote instructions)
    ✓ Reputation (tracks upvotes/downvotes received)
    ✓ Vote records (PDA prevents double-voting)
    ✓ Token balances (SPL Token program guarantees)
    ✓ Title hash (immutable once posted)
    ✓ Author attribution (wallet signed the transaction)

  {BOLD}What's NOT tamper-proof (off-chain Supabase):{RESET}
    ✗ Question/answer text bodies (can be edited in DB)
    ✗ Vote counts in Supabase (can diverge from on-chain)
    ✗ User metadata (username, etc.)

  The title_hash is the bridge: it anchors off-chain content
  to on-chain truth. For full integrity, you'd also hash the body.
""")

print(f"\n{BOLD}{GREEN}Explorer complete!{RESET}\n")
