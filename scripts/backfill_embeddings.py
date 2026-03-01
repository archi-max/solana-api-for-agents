"""Backfill embeddings for all existing questions and answers.

Usage:
    python3 scripts/backfill_embeddings.py

Requires OPENAI_API_KEY in .env and the content_embeddings table to exist.
"""

import sys
import time
import logging

sys.path.insert(0, ".")

from app.database import supabase
from app.utils.embeddings import embed_question, embed_answer

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def backfill_questions():
    """Generate embeddings for questions that don't have one yet."""
    # Get all question IDs that already have embeddings
    existing = supabase.table("content_embeddings").select("content_id").eq("content_type", "question").execute()
    existing_ids = {r["content_id"] for r in existing.data}

    # Get all questions
    questions = supabase.table("questions").select("id, title, body").execute()
    to_embed = [q for q in questions.data if q["id"] not in existing_ids]

    logger.info(f"Questions: {len(questions.data)} total, {len(existing_ids)} already embedded, {len(to_embed)} to backfill")

    for i, q in enumerate(to_embed):
        embedding = embed_question(q["title"], q["body"])
        if embedding:
            try:
                supabase.table("content_embeddings").insert({
                    "content_type": "question",
                    "content_id": q["id"],
                    "question_id": q["id"],
                    "embedding": embedding,
                    "content_text": f"{q['title']}\n\n{q['body']}",
                }).execute()
                logger.info(f"  [{i+1}/{len(to_embed)}] Embedded question: {q['title'][:60]}")
            except Exception as e:
                logger.error(f"  [{i+1}/{len(to_embed)}] Failed to store: {e}")
        else:
            logger.warning(f"  [{i+1}/{len(to_embed)}] Failed to generate embedding for: {q['title'][:60]}")

        # Rate limit: OpenAI allows ~3000 RPM for embedding, but be gentle
        if (i + 1) % 50 == 0:
            time.sleep(1)


def backfill_answers():
    """Generate embeddings for answers that don't have one yet."""
    existing = supabase.table("content_embeddings").select("content_id").eq("content_type", "answer").execute()
    existing_ids = {r["content_id"] for r in existing.data}

    answers = supabase.table("answers").select("id, body, question_id").execute()
    to_embed = [a for a in answers.data if a["id"] not in existing_ids]

    logger.info(f"Answers: {len(answers.data)} total, {len(existing_ids)} already embedded, {len(to_embed)} to backfill")

    for i, a in enumerate(to_embed):
        embedding = embed_answer(a["body"])
        if embedding:
            try:
                supabase.table("content_embeddings").insert({
                    "content_type": "answer",
                    "content_id": a["id"],
                    "question_id": a["question_id"],
                    "embedding": embedding,
                    "content_text": a["body"],
                }).execute()
                logger.info(f"  [{i+1}/{len(to_embed)}] Embedded answer {a['id'][:8]}...")
            except Exception as e:
                logger.error(f"  [{i+1}/{len(to_embed)}] Failed to store: {e}")
        else:
            logger.warning(f"  [{i+1}/{len(to_embed)}] Failed to generate embedding for answer {a['id'][:8]}")

        if (i + 1) % 50 == 0:
            time.sleep(1)


if __name__ == "__main__":
    logger.info("=== Backfilling embeddings ===")
    backfill_questions()
    backfill_answers()
    logger.info("=== Done ===")
