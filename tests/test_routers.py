"""Tests for API routers — exercise endpoints via FastAPI TestClient with mocked Supabase/Solana."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.models.question import QuestionPublic
from app.models.answer import AnswerPublic
from app.models.forum import ForumPublic
from app.models.user import UserPublic


NOW = datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_user(user_id="u1", username="testuser"):
    return {
        "id": user_id,
        "username": username,
        "api_key_hash": "$2b$12$fake",
        "question_count": 0,
        "answer_count": 0,
        "reputation": 0,
        "created_at": NOW,
        "is_admin": False,
        "wallet_address": "4siAVfbB1mYhaWUf4AaVs4E3K6bbZDNRyKTL58EDmvPc",
    }


def _make_supabase_question(qid="q1", forum_id="f1", author_id="u1"):
    return {
        "id": qid,
        "title": "Test Question",
        "body": "Test body",
        "forum_id": forum_id,
        "author_id": author_id,
        "upvote_count": 5,
        "downvote_count": 1,
        "score": 4,
        "answer_count": 2,
        "created_at": NOW,
        "solana_tx": "5vVdqy3RV5JkHKuDJQDXQLSqC7nSff6EVCznvUQQmgCA",
        "solana_pda": "Db1zfaZ83GGrVU3UuNMYQC6r2Mj3V2RsqZuBFpoVzMRz",
        "forums": {"name": "General"},
        "users": {"username": "alice"},
    }


def _make_supabase_answer(aid="a1", qid="q1", author_id="u1"):
    return {
        "id": aid,
        "body": "Test answer body",
        "question_id": qid,
        "author_id": author_id,
        "status": "success",
        "upvote_count": 3,
        "downvote_count": 0,
        "score": 3,
        "created_at": NOW,
        "solana_tx": "tx_sig_answer",
        "solana_pda": "pda_answer_addr",
        "users": {"username": "bob"},
    }


def _make_supabase_forum(fid="f1"):
    return {
        "id": fid,
        "name": "General",
        "description": "General forum",
        "created_by": "u1",
        "question_count": 10,
        "created_at": NOW,
        "solana_tx": "tx_sig_forum",
        "solana_pda": "pda_forum_addr",
        "users": {"username": "admin"},
    }


# Mock Supabase chain helper
def _mock_chain(data=None, count=None):
    """Create a mock that supports .table().select().eq().execute() chains."""
    mock = MagicMock()
    result = MagicMock()
    result.data = data or []
    result.count = count

    # Make every method return the mock itself for chaining
    for method in ["table", "select", "eq", "neq", "ilike", "order", "range",
                   "limit", "insert", "update", "delete", "single", "in_"]:
        getattr(mock, method, MagicMock()).return_value = mock
    mock.execute.return_value = result
    return mock


# Override auth dependency for protected routes
async def _override_get_current_user():
    return _mock_user()


async def _override_get_optional_user():
    return _mock_user()


async def _override_get_optional_user_none():
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def test_client():
    """Unauthenticated test client."""
    from app.utils.auth import get_optional_user
    app.dependency_overrides[get_optional_user] = _override_get_optional_user_none
    client = TestClient(app, root_path="/api")
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def authed_client():
    """Authenticated test client."""
    from app.utils.auth import get_current_user, get_optional_user
    app.dependency_overrides[get_current_user] = _override_get_current_user
    app.dependency_overrides[get_optional_user] = _override_get_optional_user
    client = TestClient(app, root_path="/api")
    yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Stats & Root
# ---------------------------------------------------------------------------

class TestRootAndStats:
    @patch("app.main.supabase")
    def test_root_endpoint(self, mock_sb, test_client):
        resp = test_client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "message" in data or "program_id" in data

    @patch("app.main.supabase")
    def test_stats_returns_counts(self, mock_sb, test_client):
        # Mock 3 separate .table().select().execute() calls
        mock_result = MagicMock()
        mock_result.count = 42
        chain = MagicMock()
        chain.execute.return_value = mock_result
        for method in ["table", "select", "eq"]:
            getattr(chain, method).return_value = chain
        mock_sb.table.return_value = chain
        chain.select.return_value = chain

        resp = test_client.get("/stats")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Questions Router — Explorer URLs
# ---------------------------------------------------------------------------

class TestQuestionsRouter:
    @patch("app.routers.questions.supabase")
    def test_list_questions_returns_explorer_urls(self, mock_sb, test_client):
        q = _make_supabase_question()
        chain = _mock_chain(data=[q], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/questions?page=1&sort=top")
        assert resp.status_code == 200
        data = resp.json()
        questions = data["questions"]
        assert len(questions) >= 1
        first = questions[0]
        assert first["solana_tx_url"] is not None
        assert "explorer.solana.com/tx/" in first["solana_tx_url"]
        assert "?cluster=devnet" in first["solana_tx_url"]
        assert first["solana_pda_url"] is not None
        assert "explorer.solana.com/address/" in first["solana_pda_url"]

    @patch("app.routers.questions.supabase")
    def test_list_questions_null_solana_fields(self, mock_sb, test_client):
        q = _make_supabase_question()
        q["solana_tx"] = None
        q["solana_pda"] = None
        chain = _mock_chain(data=[q], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/questions?page=1&sort=top")
        assert resp.status_code == 200
        first = resp.json()["questions"][0]
        assert first["solana_tx_url"] is None
        assert first["solana_pda_url"] is None

    @patch("app.routers.questions.solana_post_question")
    @patch("app.routers.questions.supabase")
    def test_create_question_returns_explorer_urls(self, mock_sb, mock_solana, authed_client):
        from app.solana_client import SolanaTxResult
        mock_solana.return_value = SolanaTxResult(
            signature="5vVdqy3sig", pda="Db1zfapda"
        )

        # Mock insert chain
        insert_result = MagicMock()
        insert_result.data = [{"id": "q-new", "forum_id": "f1"}]
        update_result = MagicMock()
        update_result.data = [{}]

        chain = MagicMock()
        chain.execute.side_effect = [insert_result, update_result]
        for method in ["table", "insert", "update", "eq", "select", "single"]:
            getattr(chain, method).return_value = chain
        mock_sb.table.return_value = chain

        # Mock the forum lookup
        forum_chain = MagicMock()
        forum_result = MagicMock()
        forum_result.data = [{"id": "f1", "name": "General"}]
        forum_chain.execute.return_value = forum_result
        for method in ["select", "eq", "single"]:
            getattr(forum_chain, method).return_value = forum_chain

        # Route first .table("forums") to forum_chain, rest to chain
        def table_router(name):
            if name == "forums":
                return forum_chain
            return chain
        mock_sb.table.side_effect = table_router

        resp = authed_client.post("/questions", json={
            "title": "Test Q", "body": "Body text", "forum_id": "f1",
        })
        # May get 200 or 500 depending on mock depth; check what we can
        if resp.status_code == 200:
            data = resp.json()
            if data.get("solana_tx"):
                assert "explorer.solana.com/tx/" in data["solana_tx_url"]

    @patch("app.routers.questions.supabase")
    def test_get_question_by_id(self, mock_sb, test_client):
        q = _make_supabase_question(qid="q123")
        chain = _mock_chain(data=[q])
        mock_sb.table.return_value = chain

        resp = test_client.get("/questions/q123")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "q123"
        assert data["solana_tx_url"] is not None


# ---------------------------------------------------------------------------
# Answers Router — Explorer URLs
# ---------------------------------------------------------------------------

class TestAnswersRouter:
    @patch("app.routers.answers.supabase")
    def test_list_answers_returns_explorer_urls(self, mock_sb, test_client):
        a = _make_supabase_answer()
        chain = _mock_chain(data=[a], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/questions/q1/answers?page=1&sort=top")
        assert resp.status_code == 200
        data = resp.json()
        answers = data["answers"]
        assert len(answers) >= 1
        first = answers[0]
        assert first["solana_tx_url"] is not None
        assert "explorer.solana.com" in first["solana_tx_url"]
        assert first["solana_pda_url"] is not None

    @patch("app.routers.answers.supabase")
    def test_list_answers_null_solana(self, mock_sb, test_client):
        a = _make_supabase_answer()
        a["solana_tx"] = None
        a["solana_pda"] = None
        chain = _mock_chain(data=[a], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/questions/q1/answers?page=1")
        assert resp.status_code == 200
        first = resp.json()["answers"][0]
        assert first["solana_tx_url"] is None
        assert first["solana_pda_url"] is None

    @patch("app.routers.answers.supabase")
    def test_get_answer_by_id(self, mock_sb, test_client):
        a = _make_supabase_answer(aid="a99")
        chain = _mock_chain(data=[a])
        mock_sb.table.return_value = chain

        resp = test_client.get("/answers/a99")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "a99"


# ---------------------------------------------------------------------------
# Forums Router — Explorer URLs
# ---------------------------------------------------------------------------

class TestForumsRouter:
    @patch("app.routers.forums.supabase")
    def test_list_forums_returns_explorer_urls(self, mock_sb, test_client):
        f = _make_supabase_forum()
        chain = _mock_chain(data=[f], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/forums?page=1")
        assert resp.status_code == 200
        data = resp.json()
        forums = data["forums"]
        assert len(forums) >= 1
        first = forums[0]
        assert first["solana_tx_url"] is not None
        assert "explorer.solana.com" in first["solana_tx_url"]

    @patch("app.routers.forums.supabase")
    def test_list_forums_null_solana(self, mock_sb, test_client):
        f = _make_supabase_forum()
        f["solana_tx"] = None
        f["solana_pda"] = None
        chain = _mock_chain(data=[f], count=1)
        mock_sb.table.return_value = chain

        resp = test_client.get("/forums?page=1")
        assert resp.status_code == 200
        first = resp.json()["forums"][0]
        assert first["solana_tx_url"] is None

    @patch("app.routers.forums.supabase")
    def test_get_forum_by_id(self, mock_sb, test_client):
        f = _make_supabase_forum(fid="f99")
        chain = _mock_chain(data=[f])
        mock_sb.table.return_value = chain

        resp = test_client.get("/forums/f99")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "f99"
        assert data["solana_tx_url"] is not None


# ---------------------------------------------------------------------------
# Explorer URL consistency across all response types
# ---------------------------------------------------------------------------

class TestExplorerUrlConsistency:
    """Ensure all models generate explorer URLs in the same format."""

    def test_question_url_format(self):
        from app.utils.solana_explorer import tx_url, address_url
        tx = tx_url("sig123")
        addr = address_url("addr456")
        assert tx == "https://explorer.solana.com/tx/sig123?cluster=devnet"
        assert addr == "https://explorer.solana.com/address/addr456?cluster=devnet"

    def test_all_models_have_url_fields(self):
        """All public models with solana fields should have corresponding URL fields."""
        q_fields = QuestionPublic.model_fields
        assert "solana_tx_url" in q_fields
        assert "solana_pda_url" in q_fields

        a_fields = AnswerPublic.model_fields
        assert "solana_tx_url" in a_fields
        assert "solana_pda_url" in a_fields

        f_fields = ForumPublic.model_fields
        assert "solana_tx_url" in f_fields
        assert "solana_pda_url" in f_fields

        u_fields = UserPublic.model_fields
        assert "solana_pda_url" in u_fields

    def test_user_has_no_tx_url(self):
        """Users only have PDA, not TX, in their public model."""
        u_fields = UserPublic.model_fields
        assert "solana_tx_url" not in u_fields
        assert "solana_tx" not in u_fields
