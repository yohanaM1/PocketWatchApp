"""plan_check: question facts and plan rejection (the spec2-implementation.md 5.2 table)."""

from datetime import date

import pytest

from app.agent.plan import ExplicitRange, FinalPlan
from app.agent.plan_check import PlanRejected, Unanswerable, check_plan, extract_facts
from app.agent.spending import SPENDING_CATEGORIES

TODAY = date(2026, 10, 19)


def P(period, categories=("Dining",), phrase="") -> FinalPlan:
    """A category_total plan."""
    cats = categories if isinstance(categories, str) else list(categories)
    return FinalPlan(intent="category_total", period=period, categories=cats, category_phrase=phrase)


def R(start, end) -> ExplicitRange:
    """An explicit range."""
    return ExplicitRange(start=date.fromisoformat(start), end=date.fromisoformat(end), explicit_reason="r")


def ok(question, plan, today=TODAY, merchants=frozenset()):
    """check_plan passes; returns the sanitized plan."""
    return check_plan(extract_facts(question, today, merchants), question, plan, today)


def rejected(question, plan, today=TODAY):
    """check_plan raises PlanRejected; returns the message."""
    with pytest.raises(PlanRejected) as e:
        ok(question, plan, today)
    return str(e.value)


def exact(question, today=TODAY):
    """The (start, end) of each exact period found."""
    return [(p.start.isoformat(), p.end.isoformat()) for p in extract_facts(question, today).exact]


# --- spec2.md section 5 worked examples ---

def test_worked_examples():
    """Plan A and B pass ("2026-09" and last_month are the same range on Oct 19); plan C names the wrong category."""
    q = "How much did I spend on dining in September?"
    ok(q, P("2026-09"))
    ok(q, P("last_month"))
    assert "names Dining" in rejected(q, P("2026-09", ["Groceries"]))


def test_last_month_on_jan_3():
    """'last month' on Jan 3 is December of the previous year."""
    ok("dining last month", P("2025-12"), today=date(2026, 1, 3))
    rejected("dining last month", P("2026-01"), today=date(2026, 1, 3))


@pytest.mark.parametrize("phrase,kept", [
    ("food", "food"), ("pizza", ""), ("on", ""), ("fo", ""), ("food 2", ""), ("foo", ""),
])
def test_category_phrase_sanitized(phrase, kept):
    """The phrase must be whole words of the question, 3+ characters, digit-free; otherwise it's blanked."""
    plan = ok("what did I spend on food 2day in September", P("2026-09", ["Dining", "Groceries"], phrase))
    assert plan.category_phrase == kept


# --- months ---

@pytest.mark.parametrize("q", ["dining in Sept 2026", "dining in sep.", "dining in September"])
def test_exact_september(q):
    """Abbreviations, a trailing dot and a year all mean exact 2026-09."""
    assert exact(q) == [("2026-09-01", "2026-09-30")]


def test_comma_year_and_last_year():
    """'September, 2025' and 'September last year' are 2025-09."""
    assert exact("dining in September, 2025") == [("2025-09-01", "2025-09-30")]
    assert exact("dining in September last year") == [("2025-09-01", "2025-09-30")]


@pytest.mark.parametrize("q", ["dining last September", "May I see my dining total?",
                               "dining this month last year", "dining the last month", "dining this past month"])
def test_no_period_constraint(q):
    """Unclear years, 'May I…' and 'the last month' add no period constraint."""
    f = extract_facts(q, TODAY)
    assert f.exact == [] and f.overlap == []


def test_may_with_preposition():
    """'in May' is the month."""
    assert exact("dining in May") == [("2026-05-01", "2026-05-31")]


def test_since_september_is_overlap():
    """'since September': Sep 1–Oct 19 passes, August fails."""
    q = "dining since September"
    ok(q, P(R("2026-09-01", "2026-10-19")))
    assert "doesn't include" in rejected(q, P("2026-08"))


@pytest.mark.parametrize("q", ["dining Sept 15 to 30", "dining first week of September"])
def test_part_of_september_is_overlap(q):
    """Day ranges and 'first week of' only require overlap."""
    f = extract_facts(q, TODAY)
    assert f.exact == [] and len(f.overlap) == 1
    ok(q, P(R("2026-09-15", "2026-09-30")))


def test_from_august_to_october():
    """Two overlaps; Aug 1–Oct 19 passes."""
    f = extract_facts("dining from August to October", TODAY)
    assert len(f.overlap) == 2 and f.exact == []
    ok("dining from August to October", P(R("2026-08-01", "2026-10-19")))


def test_last_month_september_deduplicated():
    """'last month (September)' is one exact period."""
    assert exact("dining last month (September)") == [("2026-09-01", "2026-09-30")]


# --- unanswerable ---

@pytest.mark.parametrize("q", ["September vs October", "dining compared to last month", "why did it increase",
                               "how much income did I get", "dining in September and October"])
def test_unsupported(q):
    """Comparisons, 'why', income and two exact periods get the unsupported reply."""
    with pytest.raises(Unanswerable) as e:
        extract_facts(q, TODAY)
    assert e.value.label == "unsupported"


def test_merchant_question_unsupported():
    """A merchant in the user's own data, as whole words, is unsupported; a substring isn't."""
    with pytest.raises(Unanswerable):
        extract_facts("how much at Starbucks", TODAY, frozenset({"starbucks"}))
    extract_facts("how much on dining", TODAY, frozenset({"din"}))


def test_future_month():
    """A month with an explicit future year is future_period."""
    with pytest.raises(Unanswerable) as e:
        extract_facts("dining in December 2026", TODAY)
    assert e.value.label == "future_period"


# --- categories ---

def test_other_than_rent():
    """'other than rent' excludes Rent: all_spending is rejected, the 10 others pass."""
    q = "What did I spend other than rent?"
    f = extract_facts(q, TODAY)
    assert f.exclude == {"Rent"} and f.include == set()
    assert "excludes Rent" in rejected(q, P("2026-09", "all_spending"))
    ok(q, P("2026-09", [c for c in SPENDING_CATEGORIES if c != "Rent"]))


def test_grocery_shopping():
    """'grocery shopping' is Groceries only."""
    f = extract_facts("grocery shopping in September", TODAY)
    assert f.include == {"Groceries"}
    rejected("grocery shopping in September", P("2026-09", ["Groceries", "Shopping"]))


def test_excluding_two():
    """'excluding dining and groceries' excludes both."""
    assert extract_facts("everything excluding dining and groceries", TODAY).exclude == {"Dining", "Groceries"}


def test_other_is_not_a_category():
    """'dining and other stuff' includes Dining only, and the single-category rule applies."""
    f = extract_facts("dining and other stuff", TODAY)
    assert f.include == {"Dining"}
    assert "only Dining" in rejected("dining and other stuff", P("2026-09", ["Dining", "Other"]))


def test_wrong_month_rejected_with_month_name():
    """The rejection reason names the month, for the LLM's retry."""
    assert "September 2026" in rejected("dining in September", P("2026-08"))
