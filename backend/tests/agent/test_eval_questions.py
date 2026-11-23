"""The evaluation question set is consistent with the system: each expected plan, fed to the real loop,
gives an answer the harness scores as correct. Catches a wrong expectation or oracle before a live run."""

import csv
import json
from datetime import date

import pytest

from app.agent.loop import answer_question
from app.models import VerificationStatus as VS
from app.agent.llm import FakeLLM
from scripts.eval_answers import LABELS_CSV, expected_strings, plan_matches
from scripts.eval_common import EVAL_DIR
from tests.agent.helpers import call, text

SPEC = json.loads((EVAL_DIR / "questions.json").read_text())
TODAY = date.fromisoformat(SPEC["today"])
LABELS = list(csv.DictReader(LABELS_CSV.open()))


def ideal_reply(expect: dict):
    """The tool call a perfect planner would make for this expectation."""
    if "fixed" in expect:
        label = expect["fixed"][0]
        if label == "future_period":  # the LLM plans next month; the loop turns that into the fixed reply
            return call("final_plan", intent="category_total", period="2026-12", categories="all_spending")
        return call("no_data_needed", label=label)
    period = expect["period"]
    if isinstance(period, dict):
        period = {**period, "explicit_reason": "dates named in the question"}
    return call("final_plan", intent=expect["intent"], period=period, categories=expect["categories"])


def test_thirty_questions():
    """The set is the proposal's 30 questions, ids unique."""
    ids = [q["id"] for q in SPEC["questions"]]
    assert len(ids) == 30 and len(set(ids)) == 30


@pytest.mark.parametrize("q", SPEC["questions"], ids=lambda q: q["id"])
def test_ideal_plan_scores_correct(db, fixture_user, q):
    """With the expected plan, the loop's answer is verified and has every number the answer key predicts."""
    out = answer_question(db, fixture_user[1], q["question"], FakeLLM([ideal_reply(q["expect"]), text("")]), TODAY)
    ok, why = plan_matches(q["expect"], out.plan, TODAY)
    assert ok, why
    assert out.status is VS.VERIFIED, out.failure_reason
    if "fixed" not in q["expect"]:
        missing = [w for w in expected_strings(q["expect"], LABELS, TODAY) if w not in out.answer]
        assert not missing, (missing, out.answer)
