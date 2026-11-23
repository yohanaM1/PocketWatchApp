"""POST /questions, history and single-question routes."""

import logging

from app import config
from app.agent import loop
from app.agent.llm import LLMError
from app.agent.replies import FAILED_REPLY
from app.models import QueryLog
from tests.agent.helpers import call, plan, text
from tests.conftest import register

DINING_SEP = "How much did I spend on dining in September?"


def post(client, headers, q=DINING_SEP):
    """Ask one question."""
    return client.post("/questions", headers=headers, json={"question": q})


def test_ask_verified(client, db, fixture_user, use_llm):
    """201, verified, stored with audit fields; no failure_reason in the body."""
    headers = fixture_user[0]
    use_llm([plan(), text("Nice work.")])
    r = post(client, headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "verified" and body["answer"].startswith("You spent $61.87 on Dining in September.")
    assert body["extra"] == "Nice work." and body["as_of"] == "2026-10-19" and body["attempts"] == 1
    assert body["slots"] and body["final_results"] and "failure_reason" not in body
    row = db.get(QueryLog, body["id"])
    assert row.prompt_version == config.PROMPT_VERSION and row.latency_ms is not None
    assert row.tool_calls[0]["phase"] == "final"


def test_requires_auth(client):
    """No token: 401."""
    assert post(client, {}).status_code == 401


def test_validation(client, fixture_user, use_llm):
    """Empty or overlong questions: 422."""
    use_llm([])
    assert post(client, fixture_user[0], "   ").status_code == 422
    assert post(client, fixture_user[0], "x" * 501).status_code == 422


def test_failed_stores_fixed_reply_and_truncates(client, db, fixture_user, use_llm):
    """A 2,000-character LLMError: 201, failed, FAILED_REPLY, reason truncated to 500."""
    use_llm([LLMError("x" * 2000)])
    r = post(client, fixture_user[0])
    assert r.status_code == 201 and r.json()["status"] == "failed" and r.json()["answer"] == FAILED_REPLY
    assert len(db.get(QueryLog, r.json()["id"]).failure_reason) == 500


def test_unverified_answer_is_null(client, db, fixture_user, use_llm, monkeypatch):
    """Unverified: answer null in the API, rejected text only in the database."""
    def bad_verify(*a, **k):
        """Reject everything."""
        return loop.verifier.Verdict(False, "forced")
    monkeypatch.setattr(loop.verifier, "verify", bad_verify)
    use_llm([plan()])
    body = post(client, fixture_user[0]).json()
    assert body["status"] == "unverified" and body["answer"] is None and body["final_results"]
    assert "forced" in db.get(QueryLog, body["id"]).failure_reason


def test_crash_is_failed_and_logged(client, db, fixture_user, use_llm, monkeypatch, caplog):
    """A crash inside answer_question: 201, failed, 'internal error', traceback logged."""
    def boom(*a, **k):
        """Crash."""
        raise RuntimeError("bug")
    monkeypatch.setattr("app.agent.router.answer_question", boom)
    use_llm([])
    with caplog.at_level(logging.ERROR):
        r = post(client, fixture_user[0])
    assert r.status_code == 201 and r.json()["status"] == "failed"
    assert db.get(QueryLog, r.json()["id"]).failure_reason == "internal error"
    assert "agent crashed" in caplog.text and "RuntimeError" in caplog.text


def test_history_and_paging(client, fixture_user, use_llm):
    """Newest first, before_id pages, limit=0 is 422, and history never calls the LLM."""
    headers = fixture_user[0]
    use_llm([call("no_data_needed", label="greeting")] * 3)
    ids = [post(client, headers, f"hello number {i}").json()["id"] for i in range(3)]
    fake = use_llm([])
    page = client.get("/questions?limit=2", headers=headers).json()
    assert [q["id"] for q in page] == [ids[2], ids[1]]
    older = client.get(f"/questions?limit=2&before_id={ids[1]}", headers=headers).json()
    assert [q["id"] for q in older] == [ids[0]]
    assert client.get("/questions?limit=0", headers=headers).status_code == 422
    assert all("failure_reason" not in q for q in page)
    assert fake.requests == []


def test_other_users_question_is_404(client, fixture_user, use_llm):
    """User B can't read user A's question."""
    use_llm([call("no_data_needed", label="greeting")])
    qid = post(client, fixture_user[0], "hi").json()["id"]
    assert client.get(f"/questions/{qid}", headers=fixture_user[0]).status_code == 200
    other = register(client, "b@umd.edu")
    assert client.get(f"/questions/{qid}", headers=other).status_code == 404
    assert client.get("/questions", headers=other).json() == []


def test_rate_limit(client, fixture_user, use_llm):
    """The 21st question in a minute is 429."""
    use_llm([call("no_data_needed", label="greeting")] * 20)
    for i in range(20):
        assert post(client, fixture_user[0], f"hi {i}").status_code == 201
    assert post(client, fixture_user[0], "hi again").status_code == 429


def test_unconfigured_llm_fails_cleanly(client, fixture_user):
    """With no LLM configured (CI), a question ends as failed, not a 500."""
    from app.agent.llm import get_llm
    get_llm.cache_clear()
    r = post(client, fixture_user[0])
    assert r.status_code == 201 and r.json()["status"] == "failed"
