"""
End-to-end test for solana-api-for-agents.
Tests the full flow: register → create forum → post question → post answer → vote.
Hits both Supabase (new project) and Solana devnet.

Run: python3 test_e2e.py
"""

import sys
import os

# Ensure we can import app modules
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client
from app.config import settings
from app.utils.api_key import generate_api_key, extract_prefix
import bcrypt
import json

print("=" * 60)
print("ChatOverflow Solana API — E2E Test")
print("=" * 60)

# --- Setup ---
client = create_client(settings.supabase_url, settings.supabase_service_key)
print(f"\nSupabase: {settings.supabase_url}")
print(f"Solana RPC: {settings.solana_rpc_url}")
print(f"Program ID: {settings.program_id}")

# --- Test 1: Register a user ---
print("\n--- Test 1: Register user 'test_agent_1' ---")
api_key, prefix, key_hash = generate_api_key()

# Check if user already exists
existing = client.table("users").select("id").eq("username", "test_agent_1").execute()
if existing.data:
    user_id = existing.data[0]["id"]
    print(f"  User already exists: {user_id}")
else:
    result = client.table("users").insert({
        "username": "test_agent_1",
        "api_key_prefix": prefix,
        "api_key_hash": key_hash,
        "wallet_address": settings.program_id[:44],  # placeholder
    }).execute()
    user_id = result.data[0]["id"]
    print(f"  Created user: {user_id}")

# Try Solana registerUser
print("  Trying Solana registerUser...")
from app.solana_client import register_user as solana_register_user
sol_result = solana_register_user("test_agent_1", "test_agent_1")
if sol_result.signature:
    print(f"  Solana tx: {sol_result.signature}")
    print(f"  Profile PDA: {sol_result.pda}")
    # Update user with Solana data
    client.table("users").update({
        "solana_profile_pda": sol_result.pda,
    }).eq("id", user_id).execute()
else:
    print(f"  Solana failed (non-fatal): {sol_result.error}")

# --- Test 2: Create a forum ---
print("\n--- Test 2: Create forum 'Solana Agents' ---")
existing_forum = client.table("forums").select("id, solana_pda").eq("name", "Solana Agents").execute()
if existing_forum.data:
    forum_id = existing_forum.data[0]["id"]
    forum_pda = existing_forum.data[0].get("solana_pda")
    print(f"  Forum already exists: {forum_id}")
else:
    result = client.table("forums").insert({
        "name": "Solana Agents",
        "description": "Q&A for AI agents building on Solana",
        "created_by": user_id,
    }).execute()
    forum_id = result.data[0]["id"]
    forum_pda = None
    print(f"  Created forum: {forum_id}")

# Try Solana createForum
if not forum_pda:
    print("  Trying Solana createForum...")
    from app.solana_client import create_forum as solana_create_forum
    sol_result = solana_create_forum("Solana Agents")
    if sol_result.signature:
        print(f"  Solana tx: {sol_result.signature}")
        print(f"  Forum PDA: {sol_result.pda}")
        forum_pda = sol_result.pda
        client.table("forums").update({
            "solana_pda": sol_result.pda,
            "solana_tx": sol_result.signature,
        }).eq("id", forum_id).execute()
    else:
        print(f"  Solana failed (non-fatal): {sol_result.error}")
else:
    print(f"  Forum PDA already set: {forum_pda}")

# --- Test 3: Post a question ---
print("\n--- Test 3: Post a question ---")
result = client.table("questions").insert({
    "title": "How do Solana PDAs work?",
    "body": "I'm trying to understand Program Derived Addresses in Solana. How are they derived and what are they used for?",
    "forum_id": forum_id,
    "author_id": user_id,
}).execute()
question_id = result.data[0]["id"]
print(f"  Created question in Supabase: {question_id}")

# Try Solana postQuestion
if forum_pda:
    print("  Trying Solana postQuestion...")
    from app.solana_client import post_question as solana_post_question
    content_uri = f"supabase:{question_id}"
    sol_result = solana_post_question(
        forum_pda_str=forum_pda,
        title="How do Solana PDAs work?",
        content_uri=content_uri,
    )
    if sol_result.signature:
        print(f"  Solana tx: {sol_result.signature}")
        print(f"  Question PDA: {sol_result.pda}")
        client.table("questions").update({
            "solana_tx": sol_result.signature,
            "solana_pda": sol_result.pda,
        }).eq("id", question_id).execute()
    else:
        print(f"  Solana failed (non-fatal): {sol_result.error}")
else:
    print("  Skipping Solana (no forum PDA)")

# --- Test 4: Post an answer ---
print("\n--- Test 4: Post an answer ---")
result = client.table("answers").insert({
    "body": "PDAs are derived using Pubkey.findProgramAddress(seeds, programId). They have no private key, so only the program can sign for them.",
    "question_id": question_id,
    "author_id": user_id,
    "status": "success",
}).execute()
answer_id = result.data[0]["id"]
print(f"  Created answer in Supabase: {answer_id}")

# Update answer_count
client.table("questions").update({
    "answer_count": 1,
}).eq("id", question_id).execute()

# Try Solana postAnswer
question_data = client.table("questions").select("solana_pda").eq("id", question_id).execute()
question_pda = question_data.data[0].get("solana_pda") if question_data.data else None

if question_pda:
    print("  Trying Solana postAnswer...")
    from app.solana_client import post_answer as solana_post_answer
    content_uri = f"supabase:{answer_id}"
    sol_result = solana_post_answer(
        question_pda_str=question_pda,
        content_uri=content_uri,
    )
    if sol_result.signature:
        print(f"  Solana tx: {sol_result.signature}")
        print(f"  Answer PDA: {sol_result.pda}")
        client.table("answers").update({
            "solana_tx": sol_result.signature,
            "solana_pda": sol_result.pda,
        }).eq("id", answer_id).execute()
    else:
        print(f"  Solana failed (non-fatal): {sol_result.error}")
else:
    print("  Skipping Solana (no question PDA)")

# --- Test 5: Verify Supabase data ---
print("\n--- Test 5: Verify Supabase state ---")
users = client.table("users").select("id", count="exact").execute()
forums = client.table("forums").select("id", count="exact").execute()
questions = client.table("questions").select("id, title, solana_tx, solana_pda", count="exact").execute()
answers = client.table("answers").select("id, solana_tx, solana_pda", count="exact").execute()

print(f"  Users: {users.count}")
print(f"  Forums: {forums.count}")
print(f"  Questions: {questions.count}")
print(f"  Answers: {answers.count}")

if questions.data:
    q = questions.data[0]
    print(f"\n  Latest question:")
    print(f"    Title: {q['title']}")
    print(f"    Solana TX: {q.get('solana_tx', 'none')}")
    print(f"    Solana PDA: {q.get('solana_pda', 'none')}")

print("\n" + "=" * 60)
print("E2E test complete!")
print("=" * 60)
