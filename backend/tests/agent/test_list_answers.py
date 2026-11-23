"""list_plan answers (recurring payments, open flags) through the real loop, plan check and router, on the fixture user."""

import json
from datetime import date, datetime

from sqlalchemy import select

from app.agent import replies
from app.agent.llm import FakeLLM
from app.agent.loop import answer_question
from app.agent.periods import ET
from app.agent.plan_check import RECURRING_RE, extract_facts, states_period
from app.models import Transaction
from app.models import VerificationStatus as VS
from tests.agent.cases import BY_NAME
from tests.agent.helpers import call, calls, make_fixture_user, plan, text
from tests.conftest import register

NOV5 = date(2026, 11, 5)
RECURRING = call("list_plan", intent="recurring_list")
FLAGS = call("list_plan", intent="flag_list")


def ask(db, user_id, question, script, today=NOV5):
    """Run the loop with a FakeLLM; returns (outcome, fake)."""
    fake = FakeLLM(script)
    return answer_question(db, user_id, question, fake, today), fake


def test_recurring_list_is_verified_with_no_extra_sentence(db, fixture_user):
    out, fake = ask(db, fixture_user[1], "What subscriptions do I have?", [RECURRING])
    assert out.status is VS.VERIFIED
    assert out.answer == BY_NAME["recurring_fixture"].expected_text  # the same text as the hand-built case
    assert out.extra is None and len(fake.requests) == 1  # no extra-sentence call
    assert out.plan == {"intent": "recurring_list", "as_of": "2026-11-05", "data_through": "2026-10-30"}
    assert [e["tool"] for e in out.tool_log] == ["list_plan"]


def test_flag_list_is_verified(db, fixture_user):
    out, _ = ask(db, fixture_user[1], "Do I have any duplicate or unusual charges?", [FLAGS])
    assert out.status is VS.VERIFIED
    assert out.answer.startswith("You have 3 open flags:\n- New merchant: XJ4HOLDINGS, $225.56 on Oct 24, 2026")
    assert out.answer.endswith("Review them on the Flags page.")


def test_merchant_names_never_reach_the_llm(db, fixture_user):
    _, fake = ask(db, fixture_user[1], "What subscriptions do I have?", [RECURRING])
    merchants = {m for m in db.scalars(select(Transaction.merchant)) if m}
    blob = json.dumps(fake.requests).upper()
    assert not [m for m in merchants if m in blob]


def test_dismissed_flags_leave_the_answer(client, db, fixture_user):
    headers = fixture_user[0]
    for f in client.get("/anomalies", headers=headers).json()["items"]:
        client.patch(f"/anomalies/{f['id']}", headers=headers, json={"status": "dismissed"})
    out, _ = ask(db, fixture_user[1], "Any duplicate charges?", [FLAGS])
    assert (out.status, out.answer) == (VS.VERIFIED, "You have no open flags.")


def test_list_plan_for_a_question_with_a_period_is_unsupported(db, fixture_user):
    out, _ = ask(db, fixture_user[1], "What subscriptions did I have in September?", [RECURRING])
    assert (out.status, out.answer) == (VS.VERIFIED, replies.FIXED_REPLIES["unsupported"])
    assert out.plan == {"intent": "no_data", "label": "unsupported", "source": "code"}


def test_subscriptions_question_planned_as_a_total_is_sent_back_to_list_plan(db, fixture_user):
    out, fake = ask(db, fixture_user[1], "What subscriptions do I have?",
                    [plan(period="this_month", categories=["Subscriptions"]), RECURRING])
    assert out.status is VS.VERIFIED and out.answer.startswith("You have 8 recurring payments")
    assert out.attempts == 2
    retry = json.loads(fake.requests[1]["messages"][-1]["content"])
    assert "list_plan" in retry["error"]


def test_subscriptions_in_a_month_is_still_a_category_total(db, fixture_user):
    out, _ = ask(db, fixture_user[1], "How much did I spend on subscriptions in September?",
                 [plan(period="2026-09", categories=["Subscriptions"]), text("")])
    assert out.status is VS.VERIFIED
    assert out.answer.startswith("You spent $24.46 on Subscriptions in September.")


def test_list_plan_together_with_final_plan_is_retried(db, fixture_user):
    out, fake = ask(db, fixture_user[1], "What subscriptions do I have?", [calls(RECURRING, plan()), RECURRING])
    assert out.status is VS.VERIFIED and out.answer.startswith("You have 8 recurring payments")
    assert len([m for m in fake.requests[1]["messages"] if m["role"] == "tool"]) == 2


def test_bad_list_intent_is_retried(db, fixture_user):
    out, fake = ask(db, fixture_user[1], "What subscriptions do I have?",
                    [call("list_plan", intent="subscriptions"), RECURRING])
    assert out.status is VS.VERIFIED and out.attempts == 2
    assert "intent" in json.loads(fake.requests[1]["messages"][-1]["content"])["error"]


def test_question_naming_a_merchant_is_still_refused_before_the_llm(db, fixture_user):
    out, fake = ask(db, fixture_user[1], "When is my next Netflix charge?", [RECURRING])
    assert out.answer == replies.FIXED_REPLIES["unsupported"] and fake.requests == []


def test_user_with_no_transactions(client, db):
    headers = register(client, "new@umd.edu")
    uid = client.get("/auth/me", headers=headers).json()["id"]
    out, _ = ask(db, uid, "What subscriptions do I have?", [RECURRING])
    assert (out.status, out.answer) == (
        VS.VERIFIED, "You don't have any imported transactions yet, so I can't look for recurring payments.")


def test_answers_only_use_the_askers_data(client, db, fixture_user):
    other = make_fixture_user(client, db, "other@umd.edu", labels=None)
    out, _ = ask(db, other[1], "Any duplicate charges?", [FLAGS])
    assert out.answer.startswith("You have 2 open flags:")  # no labels: the Amazon charge isn't categorized
    mine, _ = ask(db, fixture_user[1], "Any duplicate charges?", [FLAGS])
    assert mine.answer.startswith("You have 3 open flags:")


def test_router_stores_and_returns_a_list_answer(client, fixture_user, use_llm):
    use_llm([RECURRING], today=datetime(2026, 11, 5, 12, 0, tzinfo=ET))
    r = client.post("/questions", headers=fixture_user[0], json={"question": "What are my recurring payments?"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "verified" and body["plan"]["intent"] == "recurring_list"
    assert body["final_results"][0]["total"] == {"row_id": "rectotal", "amount": "-1006.96", "count": 8}
    assert client.get("/questions", headers=fixture_user[0]).json()[0]["id"] == body["id"]


def test_period_and_recurring_word_detection():
    today = NOV5
    for q in ("What subscriptions do I have?", "What are my recurring payments?", "list my monthly bills"):
        assert RECURRING_RE.search(q) and not states_period(extract_facts(q, today), q), q
    for q in ("What subscriptions did I have in September?", "subscriptions this year", "recurring charges since May",
              "my subscriptions last month"):
        assert states_period(extract_facts(q, today), q), q
