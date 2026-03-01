"""
Solana client helper for building and submitting transactions to the ChatOverflow program.

Uses solders/solana-py/anchorpy to interact with the on-chain program.
The server holds a single platform authority keypair that signs all transactions.
"""

import json
import hashlib
import logging
import os
from pathlib import Path
from dataclasses import dataclass

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.instruction import Instruction, AccountMeta
from solders.transaction import Transaction
from solders.message import Message
from solders.hash import Hash
from solana.rpc.api import Client as SolanaClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.types import TxOpts

from app.config import settings

logger = logging.getLogger(__name__)

# Well-known program addresses
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
RENT_SYSVAR_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")

# Instruction discriminators (from the IDL)
DISCRIMINATORS = {
    "register_user": bytes([2, 241, 150, 223, 99, 214, 116, 97]),
    "create_forum": bytes([233, 228, 176, 133, 191, 4, 200, 212]),
    "post_question": bytes([195, 64, 202, 177, 235, 15, 22, 206]),
    "post_answer": bytes([224, 4, 196, 238, 175, 240, 162, 218]),
    "vote_question": bytes([249, 254, 153, 12, 179, 196, 6, 238]),
    "vote_answer": bytes([222, 220, 255, 139, 204, 94, 144, 28]),
}

# Known devnet addresses
REWARD_MINT = Pubkey.from_string("FCk3KLRXWGD2KF2FzsSLE9YXQHHmwnXjLWDCxt1noRjJ")


@dataclass
class SolanaTxResult:
    """Result of a Solana transaction submission."""
    signature: str | None
    pda: str | None
    error: str | None = None


def _load_keypair() -> Keypair | None:
    """Load the platform authority keypair from the configured path."""
    try:
        keypair_path = os.path.expanduser(settings.solana_keypair_path)
        with open(keypair_path, "r") as f:
            secret_key_bytes = json.load(f)
        return Keypair.from_bytes(bytes(secret_key_bytes))
    except Exception as e:
        logger.warning(f"Failed to load Solana keypair: {e}")
        return None


def _get_program_id() -> Pubkey:
    """Get the program ID from settings."""
    return Pubkey.from_string(settings.program_id)


def _get_rpc_client() -> SolanaClient:
    """Create a Solana RPC client."""
    return SolanaClient(settings.solana_rpc_url)


# --- PDA derivation helpers ---

def find_platform_pda() -> tuple[Pubkey, int]:
    """Derive the Platform PDA."""
    return Pubkey.find_program_address(
        [b"platform"],
        _get_program_id(),
    )


def find_reward_mint_pda() -> tuple[Pubkey, int]:
    """Derive the Reward Mint PDA."""
    return Pubkey.find_program_address(
        [b"reward_mint"],
        _get_program_id(),
    )


def find_forum_pda(name: str) -> tuple[Pubkey, int]:
    """Derive a Forum PDA from the forum name."""
    return Pubkey.find_program_address(
        [b"forum", name.encode("utf-8")],
        _get_program_id(),
    )


def find_user_profile_pda(wallet: Pubkey) -> tuple[Pubkey, int]:
    """Derive a UserProfile PDA from a wallet address."""
    return Pubkey.find_program_address(
        [b"user", bytes(wallet)],
        _get_program_id(),
    )


def find_question_pda(forum: Pubkey, question_id: int) -> tuple[Pubkey, int]:
    """Derive a Question PDA from forum address and question ID."""
    id_bytes = question_id.to_bytes(8, byteorder="little")
    return Pubkey.find_program_address(
        [b"question", bytes(forum), id_bytes],
        _get_program_id(),
    )


def find_answer_pda(question: Pubkey, answer_id: int) -> tuple[Pubkey, int]:
    """Derive an Answer PDA from question address and answer ID."""
    id_bytes = answer_id.to_bytes(4, byteorder="little")
    return Pubkey.find_program_address(
        [b"answer", bytes(question), id_bytes],
        _get_program_id(),
    )


def find_vote_pda(voter: Pubkey, target: Pubkey) -> tuple[Pubkey, int]:
    """Derive a Vote PDA from voter and target (question or answer) addresses."""
    return Pubkey.find_program_address(
        [b"vote", bytes(voter), bytes(target)],
        _get_program_id(),
    )


def get_associated_token_address(wallet: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the associated token address for a wallet and mint."""
    pda, _ = Pubkey.find_program_address(
        [bytes(wallet), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )
    return pda


# --- Anchor serialization helpers ---

def _encode_string(s: str) -> bytes:
    """Encode a string in Anchor format (4-byte LE length prefix + UTF-8 bytes)."""
    encoded = s.encode("utf-8")
    return len(encoded).to_bytes(4, byteorder="little") + encoded


def _encode_vote_type(vote_type: str) -> bytes:
    """Encode a VoteType enum in Anchor format."""
    if vote_type == "up":
        return bytes([0])  # Up variant index
    elif vote_type == "down":
        return bytes([1])  # Down variant index
    else:
        raise ValueError(f"Invalid vote type: {vote_type}")


# --- Transaction builders ---

def _build_and_send_tx(
    rpc: SolanaClient,
    keypair: Keypair,
    instruction: Instruction,
    retries: int = 3,
) -> str:
    """Build, sign, and send a transaction. Returns the signature string."""
    import time

    for attempt in range(retries):
        try:
            recent_blockhash_resp = rpc.get_latest_blockhash(commitment=Finalized)
            blockhash = recent_blockhash_resp.value.blockhash

            message = Message.new_with_blockhash(
                [instruction],
                keypair.pubkey(),
                blockhash,
            )
            tx = Transaction.new_unsigned(message)
            tx.sign([keypair], blockhash)

            opts = TxOpts(
                skip_preflight=False,
                preflight_commitment=Confirmed,
            )
            result = rpc.send_transaction(tx, opts=opts)
            if result.value:
                rpc.confirm_transaction(result.value, commitment=Confirmed)
                return str(result.value)
            else:
                raise Exception(f"Transaction failed: {result}")
        except Exception as e:
            if attempt < retries - 1 and "BlockhashNotFound" in str(e):
                logger.warning(f"Blockhash not found, retrying ({attempt + 1}/{retries})...")
                time.sleep(1)
                continue
            raise


# --- Public API functions ---

def register_user(wallet_address: str, username: str) -> SolanaTxResult:
    """
    Call registerUser instruction on-chain.
    Creates a UserProfile PDA and associated token account for $OVERFLOW.
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        wallet = keypair.pubkey()
        user_profile_pda, _ = find_user_profile_pda(wallet)
        user_token_account = get_associated_token_address(wallet, REWARD_MINT)

        # Build instruction data: discriminator + username string
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
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(user_profile_pda),
        )

    except Exception as e:
        logger.error(f"Solana registerUser failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))


def create_forum(name: str) -> SolanaTxResult:
    """
    Call createForum instruction on-chain.
    Creates a Forum PDA.
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        forum_pda, _ = find_forum_pda(name)
        authority = keypair.pubkey()

        data = DISCRIMINATORS["create_forum"] + _encode_string(name)

        accounts = [
            AccountMeta(pubkey=forum_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=authority, is_signer=True, is_writable=True),
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

        ix = Instruction(program_id, data, accounts)
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(forum_pda),
        )

    except Exception as e:
        logger.error(f"Solana createForum failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))


def post_question(forum_pda_str: str, title: str, content_uri: str) -> SolanaTxResult:
    """
    Call postQuestion instruction on-chain.
    Creates a Question PDA. Requires fetching the current forum.question_count first.
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        forum_pubkey = Pubkey.from_string(forum_pda_str)
        author = keypair.pubkey()
        author_profile_pda, _ = find_user_profile_pda(author)

        # We need the current question_count from the forum account.
        # Fetch the forum account data from chain.
        forum_info = rpc.get_account_info(forum_pubkey, commitment=Confirmed)
        if not forum_info.value:
            return SolanaTxResult(signature=None, pda=None, error="Forum account not found on-chain")

        # Parse question_count from forum account data.
        # Forum layout after 8-byte discriminator:
        #   authority: 32 bytes (Pubkey)
        #   name: 4 bytes len + variable bytes (String)
        #   question_count: 8 bytes (u64 LE)
        # We need to skip discriminator + authority + name string to get question_count.
        account_data = bytes(forum_info.value.data)
        offset = 8  # skip discriminator
        offset += 32  # skip authority pubkey
        name_len = int.from_bytes(account_data[offset:offset + 4], "little")
        offset += 4 + name_len  # skip name string
        question_count = int.from_bytes(account_data[offset:offset + 8], "little")

        question_pda, _ = find_question_pda(forum_pubkey, question_count)

        # SHA-256 hash of the title
        title_hash = hashlib.sha256(title.encode("utf-8")).digest()

        # Build instruction data: discriminator + title_hash (32 bytes, no length prefix) + content_uri string
        data = DISCRIMINATORS["post_question"] + title_hash + _encode_string(content_uri)

        accounts = [
            AccountMeta(pubkey=question_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=forum_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=author_profile_pda, is_signer=False, is_writable=True),
            AccountMeta(pubkey=author, is_signer=True, is_writable=True),
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
        ]

        ix = Instruction(program_id, data, accounts)
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(question_pda),
        )

    except Exception as e:
        logger.error(f"Solana postQuestion failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))


def post_answer(question_pda_str: str, content_uri: str) -> SolanaTxResult:
    """
    Call postAnswer instruction on-chain.
    Creates an Answer PDA. Requires fetching the current question.answer_count first.
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        question_pubkey = Pubkey.from_string(question_pda_str)
        author = keypair.pubkey()
        author_profile_pda, _ = find_user_profile_pda(author)

        # Fetch the question account to get answer_count.
        # Question layout after 8-byte discriminator:
        #   author: 32 bytes
        #   forum: 32 bytes
        #   question_id: 8 bytes (u64 LE)
        #   title_hash: 32 bytes
        #   content_uri: 4 bytes len + variable
        #   score: 8 bytes (i64 LE)
        #   answer_count: 4 bytes (u32 LE)
        question_info = rpc.get_account_info(question_pubkey, commitment=Confirmed)
        if not question_info.value:
            return SolanaTxResult(signature=None, pda=None, error="Question account not found on-chain")

        account_data = bytes(question_info.value.data)
        offset = 8  # discriminator
        offset += 32  # author
        offset += 32  # forum
        offset += 8   # question_id
        offset += 32  # title_hash
        uri_len = int.from_bytes(account_data[offset:offset + 4], "little")
        offset += 4 + uri_len  # content_uri
        offset += 8  # score (i64)
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
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(answer_pda),
        )

    except Exception as e:
        logger.error(f"Solana postAnswer failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))


def vote_question(
    question_pda_str: str,
    vote_type: str,
    author_wallet_str: str,
) -> SolanaTxResult:
    """
    Call voteQuestion instruction on-chain.
    Creates a Vote PDA. Upvotes mint $OVERFLOW tokens to the question author.

    Args:
        question_pda_str: The question PDA address string.
        vote_type: "up" or "down".
        author_wallet_str: The question author's wallet address (to derive their profile + token account).
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        question_pubkey = Pubkey.from_string(question_pda_str)
        author_wallet = Pubkey.from_string(author_wallet_str)
        voter = keypair.pubkey()

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
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(vote_pda),
        )

    except Exception as e:
        logger.error(f"Solana voteQuestion failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))


def vote_answer(
    answer_pda_str: str,
    vote_type: str,
    author_wallet_str: str,
) -> SolanaTxResult:
    """
    Call voteAnswer instruction on-chain.
    Creates a Vote PDA. Upvotes mint $OVERFLOW tokens to the answer author.

    Args:
        answer_pda_str: The answer PDA address string.
        vote_type: "up" or "down".
        author_wallet_str: The answer author's wallet address.
    """
    try:
        keypair = _load_keypair()
        if not keypair:
            return SolanaTxResult(signature=None, pda=None, error="Keypair not configured")

        rpc = _get_rpc_client()
        program_id = _get_program_id()

        answer_pubkey = Pubkey.from_string(answer_pda_str)
        author_wallet = Pubkey.from_string(author_wallet_str)
        voter = keypair.pubkey()

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
        sig = _build_and_send_tx(rpc, keypair, ix)

        return SolanaTxResult(
            signature=sig,
            pda=str(vote_pda),
        )

    except Exception as e:
        logger.error(f"Solana voteAnswer failed: {e}")
        return SolanaTxResult(signature=None, pda=None, error=str(e))
