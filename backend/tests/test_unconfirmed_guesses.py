"""in_review on each transaction and review_count on GET /transactions: a low-confidence guess is shown as a guess."""

import pytest
from sqlalchemy import select

from app import config
from app.models import Account, Category, CategorySource, Transaction
from app.services.review import is_in_review, needs_review
from tests.test_review import setup_user


def guess(db, merchant, category, confidence, source=CategorySource.LLM):
    """Give every row from `merchant` a category from `source` with `confidence`."""
    category_id = db.scalar(select(Category.id).where(Category.name == category))
    for t in db.scalars(select(Transaction).where(Transaction.merchant == merchant)):
        t.category_id, t.category_source, t.category_confidence = category_id, source, confidence
    db.commit()


def by_merchant(client, headers, **params):
    page = client.get("/transactions", headers=headers, params=params).json()
    return page, {t["merchant"]: t["in_review"] for t in page["items"]}


def test_low_confidence_guesses_and_uncategorized_rows_are_in_review(client, db):
    headers, _ = setup_user(client)  # STARBUCKS x3, AMAZON x2, GIANT; no LLM in tests, so all uncategorized
    guess(db, "STARBUCKS", "Dining", 0.55)
    guess(db, "GIANT", "Groceries", 0.95)
    guess(db, "AMAZON", "Shopping", 1.0, CategorySource.USER)
    page, flags = by_merchant(client, headers)
    assert flags == {"STARBUCKS": True, "GIANT": False, "AMAZON": False}
    assert (page["review_count"], page["uncategorized_count"]) == (3, 0)  # the tile counts guesses too
    starbucks = next(t for t in page["items"] if t["merchant"] == "STARBUCKS")
    assert (starbucks["category"], starbucks["category_confidence"]) == ("Dining", pytest.approx(0.55))


def test_review_count_follows_the_filters(client, db):
    headers, _ = setup_user(client)
    guess(db, "STARBUCKS", "Dining", 0.55)
    assert by_merchant(client, headers, month="2026-10")[0]["review_count"] == 4  # 1 Starbucks guess + 3 uncategorized
    assert by_merchant(client, headers, category="Dining")[0]["review_count"] == 3


def test_confirming_a_guess_takes_it_out_of_review(client, db):
    headers, _ = setup_user(client)
    guess(db, "GIANT", "Groceries", 0.40)
    giant = db.scalar(select(Transaction.id).where(Transaction.merchant == "GIANT"))
    r = client.patch(f"/transactions/{giant}", headers=headers, json={"category": "Groceries"})
    assert r.json()["transaction"]["in_review"] is False


@pytest.mark.parametrize(("category", "source", "confidence"), [
    (None, None, None), ("Dining", CategorySource.LLM, None), ("Dining", CategorySource.LLM, 0.10),
    ("Dining", CategorySource.LLM, config.REVIEW_CONFIDENCE_THRESHOLD), ("Dining", CategorySource.LLM, 0.99),
    ("Dining", CategorySource.USER, 1.0), ("Dining", CategorySource.CORRECTION, 1.0),
])
def test_python_rule_matches_the_sql_rule(client, db, category, source, confidence):
    """is_in_review (per row, for display) and needs_review() (SQL, for queue/budgets/answers) never disagree."""
    setup_user(client)
    t = db.scalar(select(Transaction).where(Transaction.merchant == "GIANT"))
    t.category_id = db.scalar(select(Category.id).where(Category.name == category)) if category else None
    t.category_source, t.category_confidence = source, confidence
    db.commit()
    db.refresh(t)  # read the stored FLOAT back, as the API does
    in_sql = db.scalar(select(Transaction.id).join(Account).where(Transaction.id == t.id, needs_review())) is not None
    assert is_in_review(t) is in_sql
