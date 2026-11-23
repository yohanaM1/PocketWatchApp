"""The verifier catch rate, computed from generated mutations rather than claimed (spec 5.3)."""

import pytest

from app.agent.plan import LIST_INTENTS
from tests.agent.cases import CASES, render
from tests.agent.mutations import Mutation, caught, run


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_unmutated_answer_is_not_caught(case):
    """The control: an untouched answer (with or without a clean extra) passes, so 'caught' means something."""
    text, _, plan = render(case)
    assert not caught(case, Mutation(case.name, "none", "", text, None))
    # List answers never get an extra sentence, so one is always caught there.
    assert caught(case, Mutation(case.name, "none", "", text, "Nice work keeping track.")) is (
        plan["intent"] in LIST_INTENTS)


def test_catch_rate_at_least_90_percent():
    """>= 90% of generated mutations are caught, and every kind of mutation was generated."""
    muts, survivors = run()
    assert {m.kind for m in muts} >= {"digit", "swap", "sign", "format", "word", "category", "date",
                                      "drop_line", "extra", "merchant"}
    rate = 1 - len(survivors) / len(muts)
    assert rate >= 0.9, [(m.case, m.kind, m.description) for m in survivors[:20]]
