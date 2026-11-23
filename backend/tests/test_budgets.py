"""GET /budgets, PUT /budgets/{month}/{category}, DELETE /budgets/{id}, POST /budgets/copy, and the status rules."""

from datetime import date
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import select

from app.models import Category, CategorySource, Transaction
from app.services.budgets import budget_percent, budget_status
from tests.conftest import register
from tests.test_imports import checking_id

_hash = count()


def add(db, account_id, day, amount, category=None, source=CategorySource.USER, confidence=None):
    """Store one transaction directly (no import), already categorized unless category is None."""
    category_id = db.scalar(select(Category.id).where(Category.name == category)) if category else None
    db.add(Transaction(account_id=account_id, date=day, amount=Decimal(amount), raw_description="TEST",
                       merchant="TEST", category_id=category_id, category_source=source if category else None,
                       category_confidence=confidence, import_hash=f"h{next(_hash)}"))
    db.commit()


def user(client, email="a@umd.edu"):
    """A new user and their Checking account id."""
    headers = register(client, email)
    return headers, checking_id(client, headers)


def put(client, headers, month, category, limit):
    return client.put(f"/budgets/{month}/{category}", headers=headers, json={"limit": limit})


def get(client, headers, month="2026-10"):
    return client.get(f"/budgets?month={month}", headers=headers).json()


def row(page, category):
    """One category's row from a GET /budgets page."""
    return next(r for r in page["rows"] if r["category"] == category)


def budgeted(page):
    """(category, limit) for every row with a budget, in page order."""
    return [(r["category"], r["limit"]) for r in page["rows"] if r["budget_id"] is not None]


# Status rules

@pytest.mark.parametrize(("spent", "status"), [
    ("0.00", "ok"), ("79.99", "ok"), ("80.00", "near"), ("100.00", "near"), ("100.01", "over"), ("-5.00", "ok"),
])
def test_status_cutoffs(spent, status):
    """Near starts at exactly 80% of the limit; exactly the limit is still near, a cent over is over."""
    assert budget_status(Decimal(spent), Decimal("100.00")) == status


def test_percent_rounds_half_up():
    assert budget_percent(Decimal("231.75"), Decimal("200.00")) == 116  # 115.875
    assert budget_percent(Decimal("0.50"), Decimal("200.00")) == 0  # 0.25
    assert budget_percent(Decimal("1.00"), Decimal("200.00")) == 1  # 0.5 rounds up


# PUT

def test_set_budget_twice_updates_it(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 10, 5), "-50.00", "Dining")
    first = put(client, headers, "2026-10", "Dining", "200.00")
    assert first.status_code == 200
    assert first.json() == {"category": "Dining", "budget_id": first.json()["budget_id"], "limit": "200.00",
                            "spent": "50.00", "left": "150.00", "percent": 25, "status": "ok", "previous_spent": None}
    second = put(client, headers, "2026-10", "Dining", "60")
    assert (second.json()["budget_id"], second.json()["limit"], second.json()["status"]) == (
        first.json()["budget_id"], "60.00", "near")
    assert budgeted(get(client, headers)) == [("Dining", "60.00")]


@pytest.mark.parametrize("category", ["Income", "Transfers", "Pizza", "dining"])
def test_only_spending_categories(client, category):
    headers, _ = user(client)
    assert put(client, headers, "2026-10", category, "100.00").status_code == 422


@pytest.mark.parametrize("limit", ["0", "-10.00", "10.001", "1000000.01", "NaN", "abc"])
def test_bad_limits_are_rejected(client, limit):
    headers, _ = user(client)
    assert put(client, headers, "2026-10", "Dining", limit).status_code == 422


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "26-10", "2026-10-01"])
def test_bad_months_are_rejected(client, month):
    headers, _ = user(client)
    assert put(client, headers, month, "Dining", "100.00").status_code in (404, 422)
    assert client.get(f"/budgets?month={month}", headers=headers).status_code == 422


def test_needs_login(client):
    assert client.get("/budgets").status_code == 401
    assert client.put("/budgets/2026-10/Dining", json={"limit": "1.00"}).status_code == 401


# GET

def test_page_has_every_spending_category_in_order_with_totals(client, db):
    headers, account_id = user(client)
    for day, amount, category in [
        (1, "-231.75", "Dining"), (2, "-168.20", "Groceries"), (3, "-42.00", "Transport"),
        (4, "-87.30", "Shopping"), (5, "-24.00", "Entertainment"), (6, "1500.00", "Income"), (7, "-300.00", "Transfers"),
    ]:
        add(db, account_id, date(2026, 10, day), amount, category)
    put(client, headers, "2026-10", "Dining", "200.00")
    put(client, headers, "2026-10", "Groceries", "200.00")
    put(client, headers, "2026-10", "Transport", "100.00")
    put(client, headers, "2026-10", "Health", "50.00")

    page = get(client, headers)
    # Income and Transfers are never budgeted, so they aren't rows
    assert [r["category"] for r in page["rows"]] == [
        "Dining", "Groceries", "Rent", "Utilities", "Transport", "Subscriptions", "Shopping", "Entertainment",
        "Education", "Health", "Other"]
    assert [(r["category"], r["status"], r["percent"]) for r in page["rows"] if r["status"] != "none"] == [
        ("Dining", "over", 116), ("Groceries", "near", 84), ("Transport", "ok", 42), ("Health", "ok", 0)]
    assert row(page, "Dining")["left"] == "-31.75"
    assert row(page, "Shopping") == {"category": "Shopping", "budget_id": None, "limit": None, "spent": "87.30",
                                     "left": None, "percent": None, "status": "none", "previous_spent": None}
    assert (page["budgeted"], page["spent"], page["left"], page["over_count"], page["budget_count"]) == (
        "550.00", "441.95", "108.05", 1, 4)
    assert (page["month"], page["as_of"], page["pending_review_count"], page["previous_month_count"]) == (
        "2026-10", "2026-10-07", 0, 0)


def test_previous_spent_is_last_months_or_none_without_imports(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 9, 14), "-61.87", "Dining")
    add(db, account_id, date(2026, 10, 14), "-82.54", "Dining")
    october = get(client, headers)
    assert (row(october, "Dining")["previous_spent"], row(october, "Rent")["previous_spent"]) == ("61.87", "0.00")
    # nothing was imported for August, so September's comparison is unknown, not $0
    assert row(get(client, headers, "2026-09"), "Dining")["previous_spent"] is None


def test_refund_lowers_spent(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 10, 3), "-80.00", "Shopping")
    add(db, account_id, date(2026, 10, 9), "30.00", "Shopping")  # refund
    assert put(client, headers, "2026-10", "Shopping", "100.00").json()["spent"] == "50.00"


def test_month_boundaries(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 9, 30), "-1.00", "Dining")
    add(db, account_id, date(2026, 10, 1), "-10.00", "Dining")
    add(db, account_id, date(2026, 10, 31), "-20.00", "Dining")
    add(db, account_id, date(2026, 11, 1), "-100.00", "Dining")
    assert put(client, headers, "2026-10", "Dining", "100.00").json()["spent"] == "30.00"


def test_review_queue_guesses_are_not_counted_but_reported(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 10, 2), "-40.00", "Dining", CategorySource.LLM, confidence=0.95)  # confident: counts
    add(db, account_id, date(2026, 10, 3), "-25.00", "Dining", CategorySource.LLM, confidence=0.40)  # in review
    add(db, account_id, date(2026, 10, 4), "-15.00")  # uncategorized
    add(db, account_id, date(2026, 9, 4), "-15.00")  # another month
    put(client, headers, "2026-10", "Dining", "100.00")
    page = get(client, headers)
    assert row(page, "Dining")["spent"] == "40.00"
    assert page["pending_review_count"] == 2
    # the purchases the Budgets page lists for Dining are exactly the counted ones
    listed = client.get("/transactions?month=2026-10&category=Dining&counted=true", headers=headers).json()
    assert [t["amount"] for t in listed["items"]] == ["-40.00"]
    everything = client.get("/transactions?month=2026-10&category=Dining", headers=headers).json()
    assert everything["total"] == 2


def test_default_month_is_the_latest_transactions(client, db):
    headers, account_id = user(client)
    add(db, account_id, date(2026, 9, 12), "-5.00", "Dining")
    add(db, account_id, date(2026, 10, 30), "-5.00", "Dining")
    page = client.get("/budgets", headers=headers).json()
    assert (page["month"], page["as_of"]) == ("2026-10", "2026-10-30")
    today_month = date.today().strftime("%Y-%m")
    assert page["max_month"] == max("2026-10", today_month)


def test_no_transactions_defaults_to_today(client):
    headers, _ = user(client)
    page = client.get("/budgets", headers=headers).json()
    assert page["month"] == page["max_month"] == date.today().strftime("%Y-%m")
    assert (page["as_of"], budgeted(page), page["budgeted"], page["budget_count"]) == (None, [], "0.00", 0)
    assert all(r["spent"] == "0.00" and r["previous_spent"] is None for r in page["rows"])


# Isolation

def test_users_only_see_and_delete_their_own(client, db):
    a, a_account = user(client, "a@umd.edu")
    b, b_account = user(client, "b@umd.edu")
    add(db, a_account, date(2026, 10, 5), "-70.00", "Dining")
    add(db, b_account, date(2026, 10, 5), "-9.00", "Dining")
    a_budget = put(client, a, "2026-10", "Dining", "100.00").json()
    assert a_budget["spent"] == "70.00"  # B's spending isn't mixed in
    assert put(client, b, "2026-10", "Dining", "100.00").json()["spent"] == "9.00"

    assert budgeted(get(client, b)) == [("Dining", "100.00")]
    assert client.delete(f"/budgets/{a_budget['budget_id']}", headers=b).status_code == 404
    assert budgeted(get(client, a)) == [("Dining", "100.00")]


# DELETE

def test_delete(client):
    headers, _ = user(client)
    budget_id = put(client, headers, "2026-10", "Dining", "100.00").json()["budget_id"]
    assert client.delete(f"/budgets/{budget_id}", headers=headers).status_code == 204
    assert budgeted(get(client, headers)) == []
    assert client.delete(f"/budgets/{budget_id}", headers=headers).status_code == 404


# Copy

def test_copy_skips_categories_already_set(client):
    headers, _ = user(client)
    put(client, headers, "2026-09", "Dining", "200.00")
    put(client, headers, "2026-09", "Groceries", "150.00")
    put(client, headers, "2026-10", "Dining", "250.00")
    assert get(client, headers)["previous_month_count"] == 2

    result = client.post("/budgets/copy", headers=headers, json={"from_month": "2026-09", "to_month": "2026-10"})
    assert result.json() == {"copied": 1, "skipped": 1}
    assert budgeted(get(client, headers)) == [("Dining", "250.00"), ("Groceries", "150.00")]
    # September is untouched
    assert budgeted(get(client, headers, "2026-09")) == [("Dining", "200.00"), ("Groceries", "150.00")]


def test_copy_only_copies_your_own(client):
    a, _ = user(client, "a@umd.edu")
    b, _ = user(client, "b@umd.edu")
    put(client, a, "2026-09", "Dining", "200.00")
    assert client.post("/budgets/copy", headers=b, json={"from_month": "2026-09", "to_month": "2026-10"}).json() == {
        "copied": 0, "skipped": 0}


def test_copy_same_month_is_rejected(client):
    headers, _ = user(client)
    assert client.post("/budgets/copy", headers=headers,
                       json={"from_month": "2026-10", "to_month": "2026-10"}).status_code == 422
