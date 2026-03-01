"""Generate embeddings using OpenAI's text-embedding-3-small model."""

import logging
from openai import OpenAI
from app.config import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> OpenAI | None:
    global _client
    if _client is None and settings.openai_api_key:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def generate_embedding(text: str) -> list[float] | None:
    """Generate a 1536-dim embedding for the given text. Returns None on failure."""
    client = _get_client()
    if not client:
        logger.warning("OpenAI API key not configured, skipping embedding")
        return None

    try:
        text = text.replace("\n", " ").strip()[:8000]
        response = client.embeddings.create(
            input=text,
            model=settings.embedding_model,
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        return None


def embed_question(title: str, body: str) -> list[float] | None:
    """Generate embedding for a question (title + body)."""
    return generate_embedding(f"{title}\n\n{body}")


def embed_answer(body: str) -> list[float] | None:
    """Generate embedding for an answer."""
    return generate_embedding(body)
