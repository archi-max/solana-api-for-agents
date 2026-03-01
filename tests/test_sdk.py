"""Tests for chatoverflow_sdk.py — SDK client unit tests with mocked HTTP."""

import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import requests as _requests_lib
from chatoverflow_sdk import ChatOverflowClient, ChatOverflowError, __version__


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    c = ChatOverflowClient(base_url="http://localhost:8000/api")
    c._session = MagicMock()
    return c


@pytest.fixture
def authed_client():
    c = ChatOverflowClient(api_key="co_test1234_secretkey", base_url="http://localhost:8000/api")
    c._user_id = "user-uuid-1"
    c._username = "test_agent"
    c._session = MagicMock()
    return c


def _mock_response(status_code=200, json_data=None, text="", ok=True, content=b"{}"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = ok
    resp.text = text
    resp.content = content
    resp.headers = {}
    resp.json.return_value = json_data or {}
    return resp


def _setup_mock(client_fixture, status_code=200, json_data=None, **kwargs):
    """Configure the mocked session to return a specific response."""
    resp = _mock_response(status_code=status_code, json_data=json_data, **kwargs)
    client_fixture._session.request.return_value = resp
    return resp


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_exists(self):
        assert __version__ == "0.1.0"


# ---------------------------------------------------------------------------
# Constructor & Properties
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_default_base_url(self):
        c = ChatOverflowClient()
        assert c.base_url == "http://localhost:8000/api"

    def test_custom_base_url(self):
        c = ChatOverflowClient(base_url="http://localhost:9000/api/")
        assert c.base_url == "http://localhost:9000/api"

    def test_trailing_slash_stripped(self):
        c = ChatOverflowClient(base_url="http://example.com/api///")
        assert not c.base_url.endswith("/")

    def test_no_api_key_by_default(self):
        c = ChatOverflowClient()
        assert c.api_key is None
        assert c.user_id is None
        assert c.username is None

    def test_api_key_from_constructor(self):
        c = ChatOverflowClient(api_key="co_abc_secret")
        assert c.api_key == "co_abc_secret"

    def test_default_timeout(self):
        c = ChatOverflowClient()
        assert c.timeout == 30

    def test_custom_timeout(self):
        c = ChatOverflowClient(timeout=60)
        assert c.timeout == 60

    def test_default_retries(self):
        c = ChatOverflowClient()
        assert c.retries == 2

    def test_repr_unauthenticated(self):
        c = ChatOverflowClient(base_url="http://localhost:8000/api")
        assert "unauthenticated" in repr(c)
        assert "localhost:8000" in repr(c)

    def test_repr_authenticated(self):
        c = ChatOverflowClient(api_key="co_x_y", base_url="http://localhost:8000/api")
        assert "authenticated" in repr(c)


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

class TestHeaders:
    def test_headers_without_auth(self, client):
        headers = client._headers(auth_required=False)
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_headers_with_auth(self, authed_client):
        headers = authed_client._headers(auth_required=True)
        assert headers["Authorization"] == "Bearer co_test1234_secretkey"

    def test_auth_required_no_key_raises(self, client):
        with pytest.raises(ChatOverflowError) as exc:
            client._headers(auth_required=True)
        assert exc.value.status_code == 401

    def test_optional_auth_includes_key_if_present(self, authed_client):
        headers = authed_client._headers(auth_required=False)
        assert "Authorization" in headers


# ---------------------------------------------------------------------------
# Error Handling & Retries
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_chatoverflow_error_attributes(self):
        err = ChatOverflowError(404, "Not found")
        assert err.status_code == 404
        assert err.detail == "Not found"
        assert "404" in str(err)

    def test_404_raises_error(self, client):
        _setup_mock(client, status_code=404, ok=False,
                    json_data={"detail": "Question not found"}, text="Not found")
        with pytest.raises(ChatOverflowError) as exc:
            client.get_question("nonexistent")
        assert exc.value.status_code == 404

    def test_500_raises_error(self, client):
        _setup_mock(client, status_code=500, ok=False, text="Internal Server Error")
        # Disable retries for this test
        client.retries = 0
        with pytest.raises(ChatOverflowError) as exc:
            client.stats()
        assert exc.value.status_code == 500

    def test_422_validation_error(self, authed_client):
        _setup_mock(authed_client, status_code=422, ok=False,
                    json_data={"detail": "Validation error"})
        with pytest.raises(ChatOverflowError) as exc:
            authed_client.ask("", "", "f1")
        assert exc.value.status_code == 422

    def test_204_returns_empty_dict(self, client):
        _setup_mock(client, status_code=204, content=b"")
        result = client.root()
        assert result == {}

    def test_non_json_error_body(self, client):
        resp = _mock_response(status_code=502, ok=False, text="Bad Gateway")
        resp.json.side_effect = ValueError("No JSON")
        client._session.request.return_value = resp
        client.retries = 0
        with pytest.raises(ChatOverflowError) as exc:
            client.stats()
        assert "Bad Gateway" in exc.value.detail

    def test_connection_error_wrapped(self, client):
        client._session.request.side_effect = _requests_lib.ConnectionError("refused")
        client.retries = 0
        with pytest.raises(ChatOverflowError) as exc:
            client.stats()
        assert exc.value.status_code == 0
        assert "Connection failed" in exc.value.detail

    def test_timeout_error_wrapped(self, client):
        client._session.request.side_effect = _requests_lib.Timeout("timed out")
        client.retries = 0
        with pytest.raises(ChatOverflowError) as exc:
            client.stats()
        assert "timed out" in exc.value.detail

    def test_retry_on_500(self, client):
        """Should retry on 500 then succeed on second attempt."""
        fail_resp = _mock_response(status_code=500, ok=False, text="err")
        ok_resp = _mock_response(json_data={"total_users": 1})
        client._session.request.side_effect = [fail_resp, ok_resp]
        client.retries = 1
        result = client.stats()
        assert result["total_users"] == 1
        assert client._session.request.call_count == 2

    def test_retry_on_connection_error(self, client):
        """Should retry on ConnectionError then succeed."""
        ok_resp = _mock_response(json_data={"ok": True})
        client._session.request.side_effect = [
            _requests_lib.ConnectionError("refused"),
            ok_resp,
        ]
        client.retries = 1
        result = client.stats()
        assert result["ok"] is True

    def test_timeout_passed_to_request(self, client):
        client.timeout = 42
        _setup_mock(client, json_data={})
        client.root()
        call_kwargs = client._session.request.call_args.kwargs
        assert call_kwargs["timeout"] == 42


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_stores_credentials(self, client):
        _setup_mock(client, json_data={
            "api_key": "co_new12345_secretpart",
            "user": {"id": "uuid-new", "username": "new_agent"},
        })
        result = client.register("new_agent")
        assert client.api_key == "co_new12345_secretpart"
        assert client.user_id == "uuid-new"
        assert client.username == "new_agent"

    def test_register_sends_correct_body(self, client):
        _setup_mock(client, json_data={
            "api_key": "co_x_y", "user": {"id": "u1", "username": "mybot"},
        })
        client.register("mybot")
        call_kwargs = client._session.request.call_args.kwargs
        assert call_kwargs["json"] == {"username": "mybot"}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

class TestUsers:
    def test_me_caches_user_info(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "uuid-me", "username": "updated_name"})
        authed_client.me()
        assert authed_client.user_id == "uuid-me"
        assert authed_client.username == "updated_name"

    def test_get_user(self, client):
        _setup_mock(client, json_data={"id": "u1", "username": "alice"})
        result = client.get_user("u1")
        assert result["username"] == "alice"

    def test_get_user_by_username(self, client):
        _setup_mock(client, json_data={"id": "u1"})
        client.get_user_by_username("alice")
        url = client._session.request.call_args[1].get("url", client._session.request.call_args[0][1])
        assert "users/username/alice" in url

    def test_top_users_default_limit(self, client):
        _setup_mock(client, json_data=[])
        client.top_users()
        params = client._session.request.call_args.kwargs["params"]
        assert params["limit"] == 10

    def test_top_users_custom_limit(self, client):
        _setup_mock(client, json_data=[])
        client.top_users(limit=5)
        params = client._session.request.call_args.kwargs["params"]
        assert params["limit"] == 5

    def test_get_user_questions(self, client):
        _setup_mock(client, json_data={"questions": [], "page": 1})
        client.get_user_questions("u1", sort="top", page=2)
        params = client._session.request.call_args.kwargs["params"]
        assert params["sort"] == "top"
        assert params["page"] == 2

    def test_get_user_answers(self, client):
        _setup_mock(client, json_data={"answers": [], "page": 1})
        client.get_user_answers("u1")
        url = client._session.request.call_args[0][1]
        assert "users/u1/answers" in url


# ---------------------------------------------------------------------------
# Forums
# ---------------------------------------------------------------------------

class TestForums:
    def test_list_forums_default(self, client):
        _setup_mock(client, json_data={"forums": [], "page": 1})
        client.list_forums()
        params = client._session.request.call_args.kwargs["params"]
        assert params["page"] == 1
        assert "search" not in params

    def test_list_forums_with_search(self, client):
        _setup_mock(client, json_data={"forums": []})
        client.list_forums(search="solana", page=2)
        params = client._session.request.call_args.kwargs["params"]
        assert params["search"] == "solana"
        assert params["page"] == 2

    def test_get_forum(self, client):
        _setup_mock(client, json_data={"id": "f1", "name": "General"})
        result = client.get_forum("f1")
        assert result["name"] == "General"

    def test_create_forum(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "f-new", "name": "New Forum"})
        authed_client.create_forum("New Forum", "A test forum")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["name"] == "New Forum"
        assert body["description"] == "A test forum"

    def test_create_forum_requires_auth(self, client):
        with pytest.raises(ChatOverflowError) as exc:
            client.create_forum("F", "D")
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

class TestQuestions:
    def test_list_questions_defaults(self, client):
        _setup_mock(client, json_data={"questions": []})
        client.list_questions()
        params = client._session.request.call_args.kwargs["params"]
        assert params["sort"] == "top"
        assert params["page"] == 1

    def test_list_questions_with_filters(self, client):
        _setup_mock(client, json_data={"questions": []})
        client.list_questions(forum_id="f1", search="PDA", sort="newest", page=3)
        params = client._session.request.call_args.kwargs["params"]
        assert params["forum_id"] == "f1"
        assert params["search"] == "PDA"
        assert params["sort"] == "newest"
        assert params["page"] == 3

    def test_list_questions_sends_auth_if_available(self, authed_client):
        """Authenticated clients should send auth header on list_questions for user_vote."""
        _setup_mock(authed_client, json_data={"questions": []})
        authed_client.list_questions()
        headers = authed_client._session.request.call_args.kwargs["headers"]
        assert "Authorization" in headers

    def test_get_question(self, client):
        _setup_mock(client, json_data={
            "id": "q1", "title": "Test Q",
            "solana_pda_url": "https://explorer.solana.com/address/pda?cluster=devnet",
        })
        result = client.get_question("q1")
        assert result["id"] == "q1"
        assert "explorer.solana.com" in result["solana_pda_url"]

    def test_get_question_sends_auth_if_available(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "q1"})
        authed_client.get_question("q1")
        headers = authed_client._session.request.call_args.kwargs["headers"]
        assert "Authorization" in headers

    def test_ask_question(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "q-new", "title": "My Q"})
        authed_client.ask("My Q", "Details here", "forum-1")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["title"] == "My Q"
        assert body["body"] == "Details here"
        assert body["forum_id"] == "forum-1"

    def test_ask_requires_auth(self, client):
        with pytest.raises(ChatOverflowError):
            client.ask("T", "B", "f1")

    def test_unanswered(self, client):
        _setup_mock(client, json_data=[])
        client.unanswered(limit=5)
        params = client._session.request.call_args.kwargs["params"]
        assert params["limit"] == 5

    def test_vote_question(self, authed_client):
        _setup_mock(authed_client, json_data={"message": "voted"})
        authed_client.vote_question("q1", "up")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["vote"] == "up"

    def test_vote_question_down(self, authed_client):
        _setup_mock(authed_client, json_data={"message": "voted"})
        authed_client.vote_question("q1", "down")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["vote"] == "down"

    def test_vote_question_none_removes(self, authed_client):
        _setup_mock(authed_client, json_data={"message": "vote removed"})
        authed_client.vote_question("q1", "none")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["vote"] == "none"

    def test_vote_requires_auth(self, client):
        with pytest.raises(ChatOverflowError):
            client.vote_question("q1", "up")


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

class TestAnswers:
    def test_answer_question(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "a-new"})
        authed_client.answer("q1", "Here's the answer", "success")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["body"] == "Here's the answer"
        assert body["status"] == "success"

    def test_answer_attempt_status(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "a2"})
        authed_client.answer("q1", "Trying...", "attempt")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["status"] == "attempt"

    def test_answer_failure_status(self, authed_client):
        _setup_mock(authed_client, json_data={"id": "a3"})
        authed_client.answer("q1", "Didn't work", "failure")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["status"] == "failure"

    def test_answer_requires_auth(self, client):
        with pytest.raises(ChatOverflowError):
            client.answer("q1", "Body", "success")

    def test_get_answers(self, client):
        _setup_mock(client, json_data={"answers": [], "page": 1})
        client.get_answers("q1", sort="newest", page=2)
        params = client._session.request.call_args.kwargs["params"]
        assert params["sort"] == "newest"
        assert params["page"] == 2

    def test_get_answer(self, client):
        _setup_mock(client, json_data={"id": "a1"})
        result = client.get_answer("a1")
        assert result["id"] == "a1"

    def test_vote_answer(self, authed_client):
        _setup_mock(authed_client, json_data={"message": "voted"})
        authed_client.vote_answer("a1", "up")
        body = authed_client._session.request.call_args.kwargs["json"]
        assert body["vote"] == "up"

    def test_vote_answer_requires_auth(self, client):
        with pytest.raises(ChatOverflowError):
            client.vote_answer("a1", "up")


# ---------------------------------------------------------------------------
# Convenience Methods
# ---------------------------------------------------------------------------

class TestConvenienceMethods:
    def test_search_delegates_to_list_questions(self, client):
        _setup_mock(client, json_data={"questions": [], "page": 1, "total_pages": 0})
        client.search("Solana PDAs")
        params = client._session.request.call_args.kwargs["params"]
        assert params["search"] == "Solana PDAs"
        assert params["sort"] == "top"

    def test_search_with_forum_id(self, client):
        _setup_mock(client, json_data={"questions": [], "page": 1})
        client.search("PDAs", forum_id="f1")
        params = client._session.request.call_args.kwargs["params"]
        assert params["forum_id"] == "f1"

    def test_search_and_answer_success(self, authed_client):
        # First call: search returns a question. Second call: answer
        search_resp = _mock_response(json_data={
            "questions": [{"id": "q-found", "title": "PDA Question"}],
            "page": 1, "total_pages": 1,
        })
        answer_resp = _mock_response(json_data={"id": "a-new"})
        authed_client._session.request.side_effect = [search_resp, answer_resp]

        result = authed_client.search_and_answer("PDA", "Here's how PDAs work")
        assert result["id"] == "a-new"

    def test_search_and_answer_no_results(self, authed_client):
        _setup_mock(authed_client, json_data={
            "questions": [], "page": 1, "total_pages": 0,
        })
        with pytest.raises(ChatOverflowError) as exc:
            authed_client.search_and_answer("nonexistent topic", "Answer")
        assert exc.value.status_code == 404
        assert "No questions found" in exc.value.detail


# ---------------------------------------------------------------------------
# General Endpoints
# ---------------------------------------------------------------------------

class TestGeneralEndpoints:
    def test_root(self, client):
        _setup_mock(client, json_data={
            "message": "Welcome to ChatOverflow",
            "program_id": "TShUF8MeAKE46dz75je7KQEdAahdRQhS3vN7ffDoEds",
        })
        result = client.root()
        assert "message" in result

    def test_stats(self, client):
        _setup_mock(client, json_data={
            "total_users": 42, "total_questions": 100, "total_answers": 250,
        })
        result = client.stats()
        assert result["total_users"] == 42
        assert result["total_questions"] == 100

    def test_stats_url(self, client):
        _setup_mock(client, json_data={})
        client.stats()
        url = client._session.request.call_args[0][1]
        assert url.endswith("/stats")


# ---------------------------------------------------------------------------
# URL Construction
# ---------------------------------------------------------------------------

class TestUrlConstruction:
    def test_base_url_prepended(self, client):
        _setup_mock(client, json_data={})
        client.root()
        url = client._session.request.call_args[0][1]
        assert url == "http://localhost:8000/api/"

    def test_question_url(self, client):
        _setup_mock(client, json_data={})
        client.get_question("abc-123")
        url = client._session.request.call_args[0][1]
        assert url == "http://localhost:8000/api/questions/abc-123"

    def test_vote_url(self, authed_client):
        _setup_mock(authed_client, json_data={})
        authed_client.vote_answer("ans-456", "down")
        url = authed_client._session.request.call_args[0][1]
        assert url == "http://localhost:8000/api/answers/ans-456/vote"
