"""Tests for Pydantic models — validation, explorer URL fields, enums."""

import pytest
from datetime import datetime, timezone
from pydantic import ValidationError

from app.models.question import (
    QuestionCreateRequest,
    QuestionPublic,
    QuestionListResponse,
    SortOption,
    VoteOption,
    VoteRequest,
)
from app.models.answer import (
    AnswerCreateRequest,
    AnswerPublic,
    AnswerListResponse,
    AnswerStatus,
)
from app.models.forum import (
    ForumCreateRequest,
    ForumPublic,
    ForumListResponse,
)
from app.models.user import (
    UserRegisterRequest,
    UserPublic,
    UserRegisterResponse,
)


NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    def test_sort_option_values(self):
        assert SortOption.newest == "newest"
        assert SortOption.top == "top"

    def test_vote_option_values(self):
        assert VoteOption.up == "up"
        assert VoteOption.down == "down"
        assert VoteOption.none == "none"

    def test_answer_status_values(self):
        assert AnswerStatus.success == "success"
        assert AnswerStatus.attempt == "attempt"
        assert AnswerStatus.failure == "failure"

    def test_vote_request_valid(self):
        vr = VoteRequest(vote=VoteOption.up)
        assert vr.vote == "up"

    def test_vote_request_invalid(self):
        with pytest.raises(ValidationError):
            VoteRequest(vote="invalid")


# ---------------------------------------------------------------------------
# UserRegisterRequest
# ---------------------------------------------------------------------------

class TestUserRegisterRequest:
    def test_valid_username(self):
        req = UserRegisterRequest(username="agent_bot_1")
        assert req.username == "agent_bot_1"

    def test_username_too_short(self):
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="ab")

    def test_username_too_long(self):
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="a" * 31)

    def test_username_min_length(self):
        req = UserRegisterRequest(username="abcdef")
        assert len(req.username) == 6

    def test_username_max_length(self):
        req = UserRegisterRequest(username="a" * 30)
        assert len(req.username) == 30

    def test_username_with_hyphen(self):
        req = UserRegisterRequest(username="my-agent-1")
        assert req.username == "my-agent-1"

    def test_username_with_underscore(self):
        req = UserRegisterRequest(username="my_agent_1")
        assert req.username == "my_agent_1"

    def test_username_invalid_characters(self):
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="agent bot!")

    def test_username_spaces_rejected(self):
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="agent name")

    def test_username_special_chars_rejected(self):
        with pytest.raises(ValidationError):
            UserRegisterRequest(username="agent@name")


# ---------------------------------------------------------------------------
# UserPublic — explorer URL fields
# ---------------------------------------------------------------------------

class TestUserPublic:
    def _make(self, **overrides):
        defaults = dict(
            id="uuid-1", username="testuser", question_count=0,
            answer_count=0, reputation=0, created_at=NOW,
        )
        defaults.update(overrides)
        return UserPublic(**defaults)

    def test_defaults_none(self):
        u = self._make()
        assert u.wallet_address is None
        assert u.solana_pda is None
        assert u.solana_pda_url is None

    def test_solana_pda_url_set(self):
        u = self._make(
            solana_pda="2QyMi7w5YvAduRBWQJezTY3KRLWA3Di8YrS4iwDUXdR8",
            solana_pda_url="https://explorer.solana.com/address/2QyMi7w5YvAduRBWQJezTY3KRLWA3Di8YrS4iwDUXdR8?cluster=devnet",
        )
        assert "explorer.solana.com" in u.solana_pda_url
        assert u.solana_pda in u.solana_pda_url

    def test_no_solana_tx_field(self):
        """Users only have solana_pda, not solana_tx."""
        u = self._make()
        assert not hasattr(u, "solana_tx")
        assert not hasattr(u, "solana_tx_url")


# ---------------------------------------------------------------------------
# QuestionCreateRequest
# ---------------------------------------------------------------------------

class TestQuestionCreateRequest:
    def test_valid(self):
        req = QuestionCreateRequest(title="Test?", body="Body text", forum_id="uuid-f")
        assert req.title == "Test?"

    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError):
            QuestionCreateRequest(title="", body="Body", forum_id="f")

    def test_body_empty_rejected(self):
        with pytest.raises(ValidationError):
            QuestionCreateRequest(title="Title", body="", forum_id="f")

    def test_title_max_length(self):
        req = QuestionCreateRequest(title="x" * 250, body="Body", forum_id="f")
        assert len(req.title) == 250

    def test_title_over_max_rejected(self):
        with pytest.raises(ValidationError):
            QuestionCreateRequest(title="x" * 251, body="Body", forum_id="f")

    def test_body_max_length(self):
        req = QuestionCreateRequest(title="T", body="x" * 50000, forum_id="f")
        assert len(req.body) == 50000

    def test_body_over_max_rejected(self):
        with pytest.raises(ValidationError):
            QuestionCreateRequest(title="T", body="x" * 50001, forum_id="f")


# ---------------------------------------------------------------------------
# QuestionPublic — explorer URL fields
# ---------------------------------------------------------------------------

class TestQuestionPublic:
    def _make(self, **overrides):
        defaults = dict(
            id="q1", title="Test Q", body="Body", forum_id="f1",
            forum_name="General", author_id="u1", author_username="alice",
            upvote_count=0, downvote_count=0, score=0, answer_count=0,
            created_at=NOW,
        )
        defaults.update(overrides)
        return QuestionPublic(**defaults)

    def test_defaults_none(self):
        q = self._make()
        assert q.solana_tx is None
        assert q.solana_tx_url is None
        assert q.solana_pda is None
        assert q.solana_pda_url is None
        assert q.user_vote is None

    def test_with_solana_fields(self):
        tx = "5vVdqy3RV5Jk"
        pda = "Db1zfaZ83GGr"
        q = self._make(
            solana_tx=tx,
            solana_tx_url=f"https://explorer.solana.com/tx/{tx}?cluster=devnet",
            solana_pda=pda,
            solana_pda_url=f"https://explorer.solana.com/address/{pda}?cluster=devnet",
        )
        assert q.solana_tx == tx
        assert "explorer.solana.com/tx/" in q.solana_tx_url
        assert "explorer.solana.com/address/" in q.solana_pda_url
        assert "?cluster=devnet" in q.solana_tx_url
        assert "?cluster=devnet" in q.solana_pda_url

    def test_user_vote_values(self):
        q_up = self._make(user_vote="up")
        assert q_up.user_vote == "up"
        q_down = self._make(user_vote="down")
        assert q_down.user_vote == "down"
        q_none = self._make(user_vote=None)
        assert q_none.user_vote is None

    def test_serialization_includes_urls(self):
        q = self._make(
            solana_tx="sig123",
            solana_tx_url="https://explorer.solana.com/tx/sig123?cluster=devnet",
        )
        data = q.model_dump()
        assert "solana_tx_url" in data
        assert data["solana_tx_url"] == "https://explorer.solana.com/tx/sig123?cluster=devnet"


# ---------------------------------------------------------------------------
# QuestionListResponse
# ---------------------------------------------------------------------------

class TestQuestionListResponse:
    def test_empty_list(self):
        resp = QuestionListResponse(questions=[], page=1, total_pages=0)
        assert resp.questions == []
        assert resp.page == 1

    def test_with_questions(self):
        q = QuestionPublic(
            id="q1", title="T", body="B", forum_id="f1", forum_name="F",
            author_id="u1", author_username="a", upvote_count=0,
            downvote_count=0, score=0, answer_count=0, created_at=NOW,
        )
        resp = QuestionListResponse(questions=[q], page=1, total_pages=1)
        assert len(resp.questions) == 1


# ---------------------------------------------------------------------------
# AnswerCreateRequest
# ---------------------------------------------------------------------------

class TestAnswerCreateRequest:
    def test_valid(self):
        req = AnswerCreateRequest(body="Answer text", status=AnswerStatus.success)
        assert req.status == "success"

    def test_body_empty_rejected(self):
        with pytest.raises(ValidationError):
            AnswerCreateRequest(body="", status=AnswerStatus.success)

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            AnswerCreateRequest(body="Answer", status="invalid")

    def test_all_statuses(self):
        for s in ["success", "attempt", "failure"]:
            req = AnswerCreateRequest(body="A", status=s)
            assert req.status == s


# ---------------------------------------------------------------------------
# AnswerPublic — explorer URL fields
# ---------------------------------------------------------------------------

class TestAnswerPublic:
    def _make(self, **overrides):
        defaults = dict(
            id="a1", body="Answer body", question_id="q1",
            author_id="u1", author_username="bob", status="success",
            upvote_count=0, downvote_count=0, score=0, created_at=NOW,
        )
        defaults.update(overrides)
        return AnswerPublic(**defaults)

    def test_defaults_none(self):
        a = self._make()
        assert a.solana_tx is None
        assert a.solana_tx_url is None
        assert a.solana_pda is None
        assert a.solana_pda_url is None

    def test_with_solana_fields(self):
        a = self._make(
            solana_tx="txsig",
            solana_tx_url="https://explorer.solana.com/tx/txsig?cluster=devnet",
            solana_pda="pdaaddr",
            solana_pda_url="https://explorer.solana.com/address/pdaaddr?cluster=devnet",
        )
        assert "?cluster=devnet" in a.solana_tx_url
        assert "?cluster=devnet" in a.solana_pda_url

    def test_user_vote_field(self):
        a = self._make(user_vote="up")
        assert a.user_vote == "up"


# ---------------------------------------------------------------------------
# ForumCreateRequest
# ---------------------------------------------------------------------------

class TestForumCreateRequest:
    def test_valid(self):
        req = ForumCreateRequest(name="Solana Agents", description="A forum")
        assert req.name == "Solana Agents"

    def test_name_empty_rejected(self):
        with pytest.raises(ValidationError):
            ForumCreateRequest(name="", description="Desc")

    def test_name_max_length(self):
        req = ForumCreateRequest(name="x" * 250)
        assert len(req.name) == 250

    def test_name_over_max_rejected(self):
        with pytest.raises(ValidationError):
            ForumCreateRequest(name="x" * 251)

    def test_description_optional(self):
        req = ForumCreateRequest(name="NoDesc")
        assert req.description is None


# ---------------------------------------------------------------------------
# ForumPublic — explorer URL fields
# ---------------------------------------------------------------------------

class TestForumPublic:
    def _make(self, **overrides):
        defaults = dict(
            id="f1", name="General", description="General forum",
            created_by="u1", created_by_username="admin",
            question_count=0, created_at=NOW,
        )
        defaults.update(overrides)
        return ForumPublic(**defaults)

    def test_defaults_none(self):
        f = self._make()
        assert f.solana_tx is None
        assert f.solana_tx_url is None
        assert f.solana_pda is None
        assert f.solana_pda_url is None

    def test_with_solana_fields(self):
        f = self._make(
            solana_tx="sig",
            solana_tx_url="https://explorer.solana.com/tx/sig?cluster=devnet",
            solana_pda="addr",
            solana_pda_url="https://explorer.solana.com/address/addr?cluster=devnet",
        )
        assert "/tx/sig?" in f.solana_tx_url
        assert "/address/addr?" in f.solana_pda_url

    def test_serialization(self):
        f = self._make(solana_pda="pda123")
        data = f.model_dump()
        assert "solana_pda" in data
        assert "solana_pda_url" in data


# ---------------------------------------------------------------------------
# UserRegisterResponse
# ---------------------------------------------------------------------------

class TestUserRegisterResponse:
    def test_default_message(self):
        user = UserPublic(
            id="u1", username="testuser", question_count=0,
            answer_count=0, reputation=0, created_at=NOW,
        )
        resp = UserRegisterResponse(user=user, api_key="co_abc123_secret")
        assert "ChatOverflow" in resp.message
        assert "$OVERFLOW" in resp.message
        assert resp.api_key == "co_abc123_secret"
