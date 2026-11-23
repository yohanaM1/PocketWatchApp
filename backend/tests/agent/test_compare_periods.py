"""compare_periods against the fixtures/README.md oracle, plus ordering, drivers, partial months and scoping."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.agent.llm import ToolCall
from app.agent.loop import _run_exploration
from app.agent.periods import PeriodError
from app.agent.plan import ComparePeriodsArgs, ExplicitRange
from app.agent.spending import SPENDING_CATEGORIES
from app.agent.tools.compare import compare_periods
from tests.agent.helpers import make_fixture_user
from tests.agent.test_tools import FULL, README
from tests.conftest import register


def run(db, user_id, base="2026-09", current="2026-10", categories=None, today=FULL):
    """Call the tool directly."""
    return compare_periods(db, user_id, ComparePeriodsArgs(base=base, current=current, categories=categories), today)


def spending(net: str) -> Decimal:
    """README net amount -> spending."""
    return Decimal("0.00") - Decimal(net)


def test_every_delta_matches_readme(db, fixture_user):
    """Each category's change is October spending minus September spending, with the right direction."""
    res = run(db, fixture_user[1])
    by = {d["category"]: d for d in res["deltas"]}
    assert set(by) == set(SPENDING_CATEGORIES)
    for cat, (sep, oct_) in README.items():
        want = spending(oct_) - spending(sep)
        d = by[cat]
        assert Decimal(d["base_spending"]) == spending(sep) and Decimal(d["current_spending"]) == spending(oct_), cat
        assert Decimal(d["change"]) == want, cat
        assert d["direction"] == ("up" if want > 0 else "down" if want < 0 else "same"), cat
        assert d["row_id"] == f"delta:{cat}:2026-09:2026-10"
    assert by["Rent"]["change"] == "0.00" and by["Education"]["change"] == "0.00"  # never "-0.00"


def test_total_delta_and_period_blocks(db, fixture_user):
    """The total change matches the two group_by_category totals, which are returned in full."""
    res = run(db, fixture_user[1])
    sep = sum(spending(s) for s, _ in README.values())
    oct_ = sum(spending(o) for _, o in README.values())
    t = res["total_delta"]
    assert Decimal(t["base_spending"]) == sep and Decimal(t["current_spending"]) == oct_
    assert Decimal(t["change"]) == oct_ - sep and t["direction"] == "down"
    assert res["base"]["period"]["key"] == "2026-09" and res["current"]["period"]["key"] == "2026-10"
    assert res["base"]["total"]["row_id"] == "total:2026-09"  # the ids the verifier already indexes
    assert res["days"] == {"base": 30, "current": 31}


def test_sorted_by_size_of_change(db, fixture_user):
    """Biggest absolute change first, ties by name; ranks are 1..11."""
    res = run(db, fixture_user[1])
    assert [d["rank"] for d in res["deltas"]] == list(range(1, 12))
    keys = [(-abs(Decimal(d["change"])), d["category"]) for d in res["deltas"]]
    assert keys == sorted(keys)
    assert [d["category"] for d in res["deltas"][:3]] == ["Shopping", "Other", "Groceries"]


def test_drivers(db, fixture_user):
    """The top three non-zero changes get up to three largest money-out rows per period, from that period."""
    res = run(db, fixture_user[1])
    assert [d["category"] for d in res["drivers"]] == ["Shopping", "Other", "Groceries"]
    for d in res["drivers"]:
        for side, month in (("base", "2026-09"), ("current", "2026-10")):
            rows = d[side]
            assert len(rows) <= 3
            assert all(r["category"] == d["category"] and r["date"].startswith(month) for r in rows)
            assert all(Decimal(r["amount"]) < 0 for r in rows)
            assert [Decimal(r["amount"]) for r in rows] == sorted(Decimal(r["amount"]) for r in rows)
            assert all(set(r) == {"row_id", "date", "amount", "merchant", "category"} for r in rows)
    shopping = res["drivers"][0]
    assert shopping["base"][0]["amount"] == "-403.43"  # the Amazon order that drove September


@pytest.mark.parametrize("base,current", [
    ("2026-10", "2026-09"),  # reversed
    ("2026-09", "2026-09"),  # same period
    ("2026-09", ExplicitRange(start=date(2026, 9, 15), end=date(2026, 10, 10), explicit_reason="overlap")),
])
def test_base_must_come_first(db, fixture_user, base, current):
    """Overlapping or reversed periods are an `order` error."""
    with pytest.raises(PeriodError) as e:
        run(db, fixture_user[1], base, current)
    assert e.value.reason == "order"


def test_order_error_goes_back_to_the_llm(db, fixture_user):
    """Through the exploration runner, an order error is an {"error": ...} result, not an exception."""
    call = ToolCall("c1", "compare_periods", json.dumps({"base": "2026-10", "current": "2026-09"}))
    assert _run_exploration(db, fixture_user[1], FULL, call) == {"error": "base must end before current starts"}


def test_partial_current_month(db, fixture_user):
    """This month so far is compared as-is; days says how long each period is."""
    res = run(db, fixture_user[1], "last_month", "this_month", today=date(2026, 10, 19))
    assert res["days"] == {"base": 30, "current": 19}
    assert res["current"]["period"]["partial"] is True


def test_category_filter_and_selection(db, fixture_user):
    """Requested categories limit the change rows and add a selection change."""
    res = run(db, fixture_user[1], categories=["Groceries", "Dining"])
    assert sorted(d["category"] for d in res["deltas"]) == ["Dining", "Groceries"]
    sel = res["selection_delta"]
    assert sel["row_id"] == "delta:sel:Dining+Groceries:2026-09:2026-10"
    want = sum(spending(README[c][1]) - spending(README[c][0]) for c in ("Dining", "Groceries"))
    assert Decimal(sel["change"]) == want
    assert "selection_delta" not in run(db, fixture_user[1], categories=["Dining"])


def test_uncategorized_delta(client, db):
    """Without labels everything is uncategorized: every category is unchanged and the change shows up here."""
    _, user_id, _ = make_fixture_user(client, db, "raw@umd.edu", labels=None)
    res = run(db, user_id)
    assert all(d["direction"] == "same" for d in res["deltas"]) and res["drivers"] == []
    u = res["uncategorized_delta"]
    assert u["base_spending"] == "1851.45" and u["row_id"] == "delta:uncat:2026-09:2026-10"
    assert Decimal(u["change"]) == Decimal(u["current_spending"]) - Decimal("1851.45")


def test_scoping(client, db, fixture_user):
    """Another user sees none of the fixture user's spending or drivers."""
    b_id = client.get("/auth/me", headers=register(client, "b@umd.edu")).json()["id"]
    res = run(db, b_id)
    assert res["total_delta"]["change"] == "0.00" and res["drivers"] == []
    assert res["base"]["coverage"]["first_date"] is None
