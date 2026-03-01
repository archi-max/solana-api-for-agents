"""
CI End-to-End Test for ChatOverflow Solana API.

Two-agent scenario:
  - Agent A creates content (user, forum, question, answer)
  - Agent B votes on Agent A's content
  - Verifies Supabase state, on-chain state, and $OVERFLOW token rewards

Requires env vars: SUPABASE_URL, SUPABASE_SERVICE_KEY, SOLANA_KEYPAIR_PATH,
                   PROGRAM_ID, SOLANA_RPC_URL

Run: python tests/test_ci_e2e.py
"""

import sys
import os
import json
import time
import hashlib
import subprocess
import tempfile
import traceback

# Ensure we can import app modules from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.instruction import Instruction, AccountMeta
from solders.message import Message
from solders.transaction import Transaction
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solana.rpc.api import Client as SolanaClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.types import TxOpts
from supabase import create_client

from app.config import settings
from app.utils.api_key import generate_api_key
from app.solana_client import (
    DISCRIMINATORS,
    REWARD_MINT,
    TOKEN_PROGRAM_ID,
    ASSOCIATED_TOKEN_PROGRAM_ID,
    RENT_SYSVAR_ID,
    _encode_string,
    _encode_vote_type,
    find_platform_pda,
    find_reward_mint_pda,
    find_forum_pda,
    find_user_profile_pda,
    find_question_pda,
    find_answer_pda,
    find_vote_pda,
    get_associated_token_address,
    _get_program_id,
    SolanaTxResult,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RUN_ID = f"ci_{int(time.time())}"
AGENT_A_USERNAME = f"ci_agent_a_{RUN_ID}"
AGENT_B_USERNAME = f"ci_agent_b_{RUN_ID}"
FORUM_NAME = f"CI Test Forum {RUN_ID}"

# Track created IDs for cleanup
cleanup_ids: dict[str, list[str]] = {
    "answer_votes": [],
    "question_votes": [],
    "answers": [],
    "questions": [],
    "forums": [],
    "users": [],
}

# Collect transaction signatures for the summary
tx_signatures: list[tuple[str, str]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_keypair_from_file(path: str) -> Keypair:
    """Load a Solana keypair from a JSON file."""
    with open(path, "r") as f:
        secret_bytes = json.load(f)
    return Keypair.from_bytes(bytes(secret_bytes))


def create_temp_keypair() -> tuple[Keypair, str]:
    """Create a new keypair and write it to a temp file. Returns (keypair, path)."""
    kp = Keypair()
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(list(bytes(kp)), f)
    return kp, path


def airdrop_sol(address: str, amount: int = 2, retries: int = 3, delay: int = 15):
    """Airdrop SOL via the Solana CLI with retry logic."""
    for attempt in range(1, retries + 1):
        print(f"    Airdrop attempt {attempt}/{retries} for {address[:12]}...")
        result = subprocess.run(
            ["solana", "airdrop", str(amount), address, "--url", "devnet"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"    Airdrop succeeded: {result.stdout.strip()}")
            return
        print(f"    Airdrop failed: {result.stderr.strip()}")
        if attempt < retries:
            print(f"    Retrying in {delay}s...")
            time.sleep(delay)
    raise RuntimeError(f"Failed to airdrop SOL to {address} after {retries} attempts")


def build_and_send_tx(rpc: SolanaClient, keypair: Keypair, ix: Instruction, retries: int = 3) -> str:
    """Build, sign, and send a transaction. Returns signature string."""
    for attempt in range(retries):
        try:
            recent = rpc.get_latest_blockhash(commitment=Finalized)
            blockhash = recent.value.blockhash
            msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
            tx = Transaction.new_unsigned(msg)
            tx.sign([keypair], blockhash)
            opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
            result = rpc.send_transaction(tx, opts=opts)
            if result.value:
                rpc.confirm_transaction(result.value, commitment=Confirmed)
                return str(result.value)
            raise Exception(f"Transaction returned no value: {result}")
        except Exception as e:
            if attempt < retries - 1 and "BlockhashNotFound" in str(e):
                print(f"    BlockhashNotFound, retrying ({attempt + 1}/{retries})...")
                time.sleep(2)
                continue
            raise


def register_user_onchain(rpc: SolanaClient, keypair: Keypair, username: str) -> SolanaTxResult:
    """Call registerUser for a given keypair."""
    program_id = _get_program_id()
    wallet = keypair.pubkey()
    user_profile_pda, _ = find_user_profile_pda(wallet)
    user_token_account = get_associated_token_address(wallet, REWARD_MINT)

    data = DISCRIMINATORS["register_user"] + _encode_string(username)
    accounts = [
        AccountMeta(pubkey=user_profile_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=wallet, is_signer=True, is_writable=True),
        AccountMeta(pubkey=REWARD_MINT, is_signer=False, is_writable=False),
        AccountMeta(pubkey=user_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=ASSOCIATED_TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(user_profile_pda))


def create_forum_onchain(rpc: SolanaClient, keypair: Keypair, name: str) -> SolanaTxResult:
    """Call createForum for a given keypair."""
    program_id = _get_program_id()
    forum_pda, _ = find_forum_pda(name)
    data = DISCRIMINATORS["create_forum"] + _encode_string(name)
    accounts = [
        AccountMeta(pubkey=forum_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=keypair.pubkey(), is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(forum_pda))


def post_question_onchain(
    rpc: SolanaClient, keypair: Keypair, forum_pda_str: str, title: str, content_uri: str
) -> SolanaTxResult:
    """Call postQuestion for a given keypair."""
    program_id = _get_program_id()
    forum_pubkey = Pubkey.from_string(forum_pda_str)
    author = keypair.pubkey()
    author_profile_pda, _ = find_user_profile_pda(author)

    # Read forum account to get question_count
    forum_info = rpc.get_account_info(forum_pubkey, commitment=Confirmed)
    if not forum_info.value:
        raise RuntimeError("Forum account not found on-chain")

    account_data = bytes(forum_info.value.data)
    offset = 8 + 32  # skip discriminator + authority
    name_len = int.from_bytes(account_data[offset:offset + 4], "little")
    offset += 4 + name_len
    question_count = int.from_bytes(account_data[offset:offset + 8], "little")

    question_pda, _ = find_question_pda(forum_pubkey, question_count)
    title_hash = hashlib.sha256(title.encode("utf-8")).digest()
    data = DISCRIMINATORS["post_question"] + title_hash + _encode_string(content_uri)

    accounts = [
        AccountMeta(pubkey=question_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=forum_pubkey, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_profile_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(question_pda))


def post_answer_onchain(
    rpc: SolanaClient, keypair: Keypair, question_pda_str: str, content_uri: str
) -> SolanaTxResult:
    """Call postAnswer for a given keypair."""
    program_id = _get_program_id()
    question_pubkey = Pubkey.from_string(question_pda_str)
    author = keypair.pubkey()
    author_profile_pda, _ = find_user_profile_pda(author)

    # Read question account to get answer_count
    question_info = rpc.get_account_info(question_pubkey, commitment=Confirmed)
    if not question_info.value:
        raise RuntimeError("Question account not found on-chain")

    account_data = bytes(question_info.value.data)
    offset = 8 + 32 + 32 + 8 + 32  # disc + author + forum + question_id + title_hash
    uri_len = int.from_bytes(account_data[offset:offset + 4], "little")
    offset += 4 + uri_len + 8  # content_uri + score(i64)
    answer_count = int.from_bytes(account_data[offset:offset + 4], "little")

    answer_pda, _ = find_answer_pda(question_pubkey, answer_count)
    data = DISCRIMINATORS["post_answer"] + _encode_string(content_uri)

    accounts = [
        AccountMeta(pubkey=answer_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=question_pubkey, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_profile_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author, is_signer=True, is_writable=True),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(answer_pda))


def vote_question_onchain(
    rpc: SolanaClient,
    voter_keypair: Keypair,
    question_pda_str: str,
    vote_type: str,
    author_wallet: Pubkey,
) -> SolanaTxResult:
    """Call voteQuestion with a specific voter keypair."""
    program_id = _get_program_id()
    question_pubkey = Pubkey.from_string(question_pda_str)
    voter = voter_keypair.pubkey()

    vote_pda, _ = find_vote_pda(voter, question_pubkey)
    author_profile_pda, _ = find_user_profile_pda(author_wallet)
    platform_pda, _ = find_platform_pda()
    reward_mint_pda, _ = find_reward_mint_pda()
    author_token_account = get_associated_token_address(author_wallet, REWARD_MINT)

    data = DISCRIMINATORS["vote_question"] + _encode_vote_type(vote_type)
    accounts = [
        AccountMeta(pubkey=vote_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=question_pubkey, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_profile_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=platform_pda, is_signer=False, is_writable=False),
        AccountMeta(pubkey=reward_mint_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=voter, is_signer=True, is_writable=True),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, voter_keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(vote_pda))


def vote_answer_onchain(
    rpc: SolanaClient,
    voter_keypair: Keypair,
    answer_pda_str: str,
    vote_type: str,
    author_wallet: Pubkey,
) -> SolanaTxResult:
    """Call voteAnswer with a specific voter keypair."""
    program_id = _get_program_id()
    answer_pubkey = Pubkey.from_string(answer_pda_str)
    voter = voter_keypair.pubkey()

    vote_pda, _ = find_vote_pda(voter, answer_pubkey)
    author_profile_pda, _ = find_user_profile_pda(author_wallet)
    platform_pda, _ = find_platform_pda()
    reward_mint_pda, _ = find_reward_mint_pda()
    author_token_account = get_associated_token_address(author_wallet, REWARD_MINT)

    data = DISCRIMINATORS["vote_answer"] + _encode_vote_type(vote_type)
    accounts = [
        AccountMeta(pubkey=vote_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=answer_pubkey, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_profile_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=platform_pda, is_signer=False, is_writable=False),
        AccountMeta(pubkey=reward_mint_pda, is_signer=False, is_writable=True),
        AccountMeta(pubkey=author_token_account, is_signer=False, is_writable=True),
        AccountMeta(pubkey=voter, is_signer=True, is_writable=True),
        AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
    ]
    ix = Instruction(program_id, data, accounts)
    sig = build_and_send_tx(rpc, voter_keypair, ix)
    return SolanaTxResult(signature=sig, pda=str(vote_pda))


def get_token_balance(rpc: SolanaClient, wallet: Pubkey) -> int:
    """Get the $OVERFLOW token balance (raw units) for a wallet."""
    ata = get_associated_token_address(wallet, REWARD_MINT)
    resp = rpc.get_token_account_balance(ata, commitment=Confirmed)
    if resp.value:
        return int(resp.value.amount)
    return 0


def get_question_score_onchain(rpc: SolanaClient, question_pda_str: str) -> int:
    """Read the score field from a Question PDA on-chain."""
    question_pubkey = Pubkey.from_string(question_pda_str)
    info = rpc.get_account_info(question_pubkey, commitment=Confirmed)
    if not info.value:
        raise RuntimeError("Question account not found on-chain")
    data = bytes(info.value.data)
    # Layout: disc(8) + author(32) + forum(32) + question_id(8) + title_hash(32) + content_uri(4+len) + score(i64)
    offset = 8 + 32 + 32 + 8 + 32
    uri_len = int.from_bytes(data[offset:offset + 4], "little")
    offset += 4 + uri_len
    score = int.from_bytes(data[offset:offset + 8], "little", signed=True)
    return score


def get_answer_score_onchain(rpc: SolanaClient, answer_pda_str: str) -> int:
    """Read the score field from an Answer PDA on-chain."""
    answer_pubkey = Pubkey.from_string(answer_pda_str)
    info = rpc.get_account_info(answer_pubkey, commitment=Confirmed)
    if not info.value:
        raise RuntimeError("Answer account not found on-chain")
    data = bytes(info.value.data)
    # Layout: disc(8) + author(32) + question(32) + answer_id(4) + content_uri(4+len) + score(i64)
    offset = 8 + 32 + 32 + 4
    uri_len = int.from_bytes(data[offset:offset + 4], "little")
    offset += 4 + uri_len
    score = int.from_bytes(data[offset:offset + 8], "little", signed=True)
    return score


def cleanup(supabase_client):
    """Delete all test data from Supabase in reverse dependency order."""
    print("\n--- Cleanup: removing test data from Supabase ---")
    for table in ["answer_votes", "question_votes", "answers", "questions", "forums", "users"]:
        ids = cleanup_ids.get(table, [])
        if not ids:
            continue
        try:
            if table in ("answer_votes", "question_votes"):
                # Composite PK tables: ids are stored as "user_id:entity_id"
                for composite in ids:
                    user_id, entity_id = composite.split(":")
                    col = "question_id" if table == "question_votes" else "answer_id"
                    supabase_client.table(table).delete().eq(
                        "user_id", user_id
                    ).eq(col, entity_id).execute()
            else:
                for eid in ids:
                    supabase_client.table(table).delete().eq("id", eid).execute()
            print(f"  Cleaned {table}: {len(ids)} rows")
        except Exception as e:
            print(f"  Warning: cleanup of {table} failed: {e}")


# ---------------------------------------------------------------------------
# Main test flow
# ---------------------------------------------------------------------------

def main():
    print("=" * 70)
    print("ChatOverflow Solana API -- CI E2E Test (Two-Agent Scenario)")
    print("=" * 70)
    print(f"Run ID: {RUN_ID}")
    print(f"Supabase URL: {settings.supabase_url}")
    print(f"Solana RPC: {settings.solana_rpc_url}")
    print(f"Program ID: {settings.program_id}")

    # ---- Clients ----
    supabase = create_client(settings.supabase_url, settings.supabase_service_key)
    rpc = SolanaClient(settings.solana_rpc_url)

    # ---- Keypairs ----
    print("\n--- Setup: Keypairs ---")
    keypair_a = load_keypair_from_file(os.path.expanduser(settings.solana_keypair_path))
    print(f"  Agent A wallet: {keypair_a.pubkey()}")

    keypair_b, keypair_b_path = create_temp_keypair()
    print(f"  Agent B wallet: {keypair_b.pubkey()}")

    # Airdrop to Agent B
    print("\n--- Setup: Airdrop SOL to Agent B ---")
    airdrop_sol(str(keypair_b.pubkey()), amount=2, retries=3, delay=15)
    # Small pause to let airdrop settle
    time.sleep(5)

    try:
        # ==================================================================
        # Step 1: Agent A registers in Supabase
        # ==================================================================
        print("\n--- Step 1: Agent A registers in Supabase ---")
        api_key_a, prefix_a, hash_a = generate_api_key()
        result = supabase.table("users").insert({
            "username": AGENT_A_USERNAME,
            "api_key_prefix": prefix_a,
            "api_key_hash": hash_a,
            "wallet_address": str(keypair_a.pubkey()),
        }).execute()
        user_a_id = result.data[0]["id"]
        cleanup_ids["users"].append(user_a_id)
        print(f"  User A ID: {user_a_id}")

        # ==================================================================
        # Step 2: Agent A registers on Solana
        # ==================================================================
        print("\n--- Step 2: Agent A registers on Solana ---")
        reg_a = register_user_onchain(rpc, keypair_a, AGENT_A_USERNAME)
        assert reg_a.signature, f"Agent A registerUser failed: {reg_a.error}"
        tx_signatures.append(("Agent A registerUser", reg_a.signature))
        print(f"  TX: {reg_a.signature}")
        print(f"  Profile PDA: {reg_a.pda}")

        # Update Supabase with PDA
        supabase.table("users").update({
            "solana_profile_pda": reg_a.pda,
        }).eq("id", user_a_id).execute()

        # ==================================================================
        # Step 3: Agent A creates forum in Supabase
        # ==================================================================
        print("\n--- Step 3: Agent A creates forum in Supabase ---")
        result = supabase.table("forums").insert({
            "name": FORUM_NAME,
            "description": "CI test forum - ephemeral",
            "created_by": user_a_id,
        }).execute()
        forum_id = result.data[0]["id"]
        cleanup_ids["forums"].append(forum_id)
        print(f"  Forum ID: {forum_id}")

        # ==================================================================
        # Step 4: Agent A creates forum on Solana
        # ==================================================================
        print("\n--- Step 4: Agent A creates forum on Solana ---")
        forum_result = create_forum_onchain(rpc, keypair_a, FORUM_NAME)
        assert forum_result.signature, f"createForum failed: {forum_result.error}"
        tx_signatures.append(("createForum", forum_result.signature))
        forum_pda = forum_result.pda
        print(f"  TX: {forum_result.signature}")
        print(f"  Forum PDA: {forum_pda}")

        supabase.table("forums").update({
            "solana_pda": forum_pda,
            "solana_tx": forum_result.signature,
        }).eq("id", forum_id).execute()

        # ==================================================================
        # Step 5: Agent A posts a question
        # ==================================================================
        print("\n--- Step 5: Agent A posts a question ---")
        question_title = "How do Solana PDAs work?"
        question_body = "Explain Program Derived Addresses, their derivation and use cases."

        result = supabase.table("questions").insert({
            "title": question_title,
            "body": question_body,
            "forum_id": forum_id,
            "author_id": user_a_id,
        }).execute()
        question_id = result.data[0]["id"]
        cleanup_ids["questions"].append(question_id)
        print(f"  Supabase question ID: {question_id}")

        q_result = post_question_onchain(
            rpc, keypair_a, forum_pda, question_title, f"supabase:{question_id}"
        )
        assert q_result.signature, f"postQuestion failed: {q_result.error}"
        tx_signatures.append(("postQuestion", q_result.signature))
        question_pda = q_result.pda
        print(f"  TX: {q_result.signature}")
        print(f"  Question PDA: {question_pda}")

        supabase.table("questions").update({
            "solana_tx": q_result.signature,
            "solana_pda": question_pda,
            "title_hash": hashlib.sha256(question_title.encode()).hexdigest(),
        }).eq("id", question_id).execute()

        # ==================================================================
        # Step 6: Agent A posts an answer
        # ==================================================================
        print("\n--- Step 6: Agent A posts an answer ---")
        answer_body = "PDAs are derived with Pubkey.findProgramAddress(seeds, programId). They have no private key."

        result = supabase.table("answers").insert({
            "body": answer_body,
            "question_id": question_id,
            "author_id": user_a_id,
            "status": "success",
        }).execute()
        answer_id = result.data[0]["id"]
        cleanup_ids["answers"].append(answer_id)
        print(f"  Supabase answer ID: {answer_id}")

        a_result = post_answer_onchain(
            rpc, keypair_a, question_pda, f"supabase:{answer_id}"
        )
        assert a_result.signature, f"postAnswer failed: {a_result.error}"
        tx_signatures.append(("postAnswer", a_result.signature))
        answer_pda = a_result.pda
        print(f"  TX: {a_result.signature}")
        print(f"  Answer PDA: {answer_pda}")

        supabase.table("answers").update({
            "solana_tx": a_result.signature,
            "solana_pda": answer_pda,
        }).eq("id", answer_id).execute()

        supabase.table("questions").update({
            "answer_count": 1,
        }).eq("id", question_id).execute()

        # ==================================================================
        # Step 7: Agent B registers in Supabase
        # ==================================================================
        print("\n--- Step 7: Agent B registers in Supabase ---")
        api_key_b, prefix_b, hash_b = generate_api_key()
        result = supabase.table("users").insert({
            "username": AGENT_B_USERNAME,
            "api_key_prefix": prefix_b,
            "api_key_hash": hash_b,
            "wallet_address": str(keypair_b.pubkey()),
        }).execute()
        user_b_id = result.data[0]["id"]
        cleanup_ids["users"].append(user_b_id)
        print(f"  User B ID: {user_b_id}")

        # ==================================================================
        # Step 8: Agent B registers on Solana
        # ==================================================================
        print("\n--- Step 8: Agent B registers on Solana ---")
        reg_b = register_user_onchain(rpc, keypair_b, AGENT_B_USERNAME)
        assert reg_b.signature, f"Agent B registerUser failed: {reg_b.error}"
        tx_signatures.append(("Agent B registerUser", reg_b.signature))
        print(f"  TX: {reg_b.signature}")
        print(f"  Profile PDA: {reg_b.pda}")

        supabase.table("users").update({
            "solana_profile_pda": reg_b.pda,
        }).eq("id", user_b_id).execute()

        # ==================================================================
        # Step 9: Agent B upvotes Agent A's question
        # ==================================================================
        print("\n--- Step 9: Agent B upvotes Agent A's question ---")

        # Record token balance before voting
        balance_before = get_token_balance(rpc, keypair_a.pubkey())
        print(f"  Agent A token balance before votes: {balance_before}")

        vq_result = vote_question_onchain(
            rpc, keypair_b, question_pda, "up", keypair_a.pubkey()
        )
        assert vq_result.signature, f"voteQuestion failed: {vq_result.error}"
        tx_signatures.append(("voteQuestion (up)", vq_result.signature))
        print(f"  TX: {vq_result.signature}")
        print(f"  Vote PDA: {vq_result.pda}")

        # Update Supabase vote counts
        supabase.table("question_votes").insert({
            "user_id": user_b_id,
            "question_id": question_id,
            "vote_type": "up",
            "solana_tx": vq_result.signature,
            "solana_vote_pda": vq_result.pda,
        }).execute()
        cleanup_ids["question_votes"].append(f"{user_b_id}:{question_id}")

        supabase.rpc("update_question_vote_counts", {
            "p_question_id": question_id,
            "p_upvote_delta": 1,
            "p_downvote_delta": 0,
        }).execute()

        # ==================================================================
        # Step 10: Agent B upvotes Agent A's answer
        # ==================================================================
        print("\n--- Step 10: Agent B upvotes Agent A's answer ---")
        va_result = vote_answer_onchain(
            rpc, keypair_b, answer_pda, "up", keypair_a.pubkey()
        )
        assert va_result.signature, f"voteAnswer failed: {va_result.error}"
        tx_signatures.append(("voteAnswer (up)", va_result.signature))
        print(f"  TX: {va_result.signature}")
        print(f"  Vote PDA: {va_result.pda}")

        supabase.table("answer_votes").insert({
            "user_id": user_b_id,
            "answer_id": answer_id,
            "vote_type": "up",
            "solana_tx": va_result.signature,
            "solana_vote_pda": va_result.pda,
        }).execute()
        cleanup_ids["answer_votes"].append(f"{user_b_id}:{answer_id}")

        supabase.rpc("update_answer_vote_counts", {
            "p_answer_id": answer_id,
            "p_upvote_delta": 1,
            "p_downvote_delta": 0,
        }).execute()

        # ==================================================================
        # Verification
        # ==================================================================
        print("\n--- Verification ---")

        # 11. Check question score in Supabase
        q_data = supabase.table("questions").select("score").eq("id", question_id).execute()
        db_question_score = q_data.data[0]["score"]
        print(f"  Supabase question score: {db_question_score}")
        assert db_question_score == 1, f"Expected question score 1, got {db_question_score}"

        # 12. Check answer score in Supabase
        a_data = supabase.table("answers").select("score").eq("id", answer_id).execute()
        db_answer_score = a_data.data[0]["score"]
        print(f"  Supabase answer score: {db_answer_score}")
        assert db_answer_score == 1, f"Expected answer score 1, got {db_answer_score}"

        # On-chain score verification
        onchain_q_score = get_question_score_onchain(rpc, question_pda)
        print(f"  On-chain question score: {onchain_q_score}")
        assert onchain_q_score == 1, f"Expected on-chain question score 1, got {onchain_q_score}"

        onchain_a_score = get_answer_score_onchain(rpc, answer_pda)
        print(f"  On-chain answer score: {onchain_a_score}")
        assert onchain_a_score == 1, f"Expected on-chain answer score 1, got {onchain_a_score}"

        # 13. Check $OVERFLOW token balance
        balance_after = get_token_balance(rpc, keypair_a.pubkey())
        tokens_earned = balance_after - balance_before
        print(f"  Agent A token balance after votes: {balance_after}")
        print(f"  Tokens earned this test: {tokens_earned} raw units")
        # 2 upvotes * 10,000,000 = 20,000,000 raw units
        assert tokens_earned == 20_000_000, (
            f"Expected 20,000,000 raw token units (2 upvotes * 10M), got {tokens_earned}"
        )
        print(f"  Token reward verified: {tokens_earned / 1_000_000:.0f} $OVERFLOW")

        # ==================================================================
        # Summary
        # ==================================================================
        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"  Agent A wallet: {keypair_a.pubkey()}")
        print(f"  Agent B wallet: {keypair_b.pubkey()}")
        print(f"  Forum PDA: {forum_pda}")
        print(f"  Question PDA: {question_pda}")
        print(f"  Answer PDA: {answer_pda}")
        print(f"  Question score: {onchain_q_score} (Supabase: {db_question_score})")
        print(f"  Answer score: {onchain_a_score} (Supabase: {db_answer_score})")
        print(f"  $OVERFLOW earned: {tokens_earned / 1_000_000:.0f} tokens ({tokens_earned} raw)")
        print(f"\n  Transactions ({len(tx_signatures)}):")
        for label, sig in tx_signatures:
            print(f"    {label}: {sig}")

        print("\n" + "=" * 70)
        print("ALL ASSERTIONS PASSED")
        print("=" * 70)

    except Exception as e:
        print(f"\n{'!' * 70}")
        print(f"TEST FAILED: {e}")
        print(f"{'!' * 70}")
        traceback.print_exc()
        raise
    finally:
        cleanup(supabase)
        # Clean up temp keypair file
        try:
            os.unlink(keypair_b_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
