"""The seed script's demo extras (saved rules and review rows) and its reset."""

from datetime import date

from sqlalchemy import func, select

from app.agent.plan import GroupByCategoryArgs
from app.agent.tools.spending import group_by_category
from app.devdata import DEMO_REVIEW_ROWS, DEMO_RULES, add_demo_extras, apply_labels, delete_user
from app.models import Account, AnomalyFlag, CategoryCorrection, CategorySource, Transaction, User
from tests.conftest import register
from tests.test_imports import checking_id, upload
from tests.test_insights_store import FIXTURE, LABELS

TODAY = date(2026, 11, 5)


def seeded(client, db, email="demo@umd.edu"):
    """What the seed script builds: the fixture imported and labeled, then the extras. Returns (headers, account id)."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    assert upload(client, headers, account_id, FIXTURE).status_code == 200
    apply_labels(db, account_id, FIXTURE, LABELS)
    add_demo_extras(db, account_id)
    db.commit()
    return headers, account_id


def totals(db, account_id):
    user_id = db.scalar(select(Account.user_id).where(Account.id == account_id))
    return {month: {r["category"]: r["net_amount"] for r in
                    group_by_category(db, user_id, GroupByCategoryArgs(period=month), TODAY)["rows"]}
            for month in ("2026-09", "2026-10")}


def test_extras_change_no_spending_total(client, db):
    plain_headers = register(client, "plain@umd.edu")
    plain = checking_id(client, plain_headers)
    upload(client, plain_headers, plain, FIXTURE)
    apply_labels(db, plain, FIXTURE, LABELS)
    db.commit()
    _, demo = seeded(client, db)
    assert totals(db, demo) == totals(db, plain)
    assert totals(db, demo)["2026-10"]["Dining"] == "-82.54"  # fixtures/README.md


def test_extras_fill_the_review_queue_and_saved_rules(client, db):
    headers, _ = seeded(client, db)
    queue = client.get("/review", headers=headers).json()
    assert (queue["total_transactions"], {g["merchant"] for g in queue["groups"]}) == (
        4, {"CHIPOTLE", "TERP THRIFT COLLEGE PARK", "UMD BOOKSTORE"})
    rules = client.get("/corrections", headers=headers).json()
    assert [(r["merchant"], r["category"], r["transaction_count"], r["mixed"]) for r in rules] == [
        ("AMAZON", "Shopping", 2, True), ("STARBUCKS", "Dining", 6, False), ("UMD DINING", "Dining", 4, False)]


def test_rule_rows_are_marked_as_the_rules(client, db):
    _, account_id = seeded(client, db)
    sources = set(db.scalars(select(Transaction.category_source)
                             .where(Transaction.account_id == account_id, Transaction.merchant.in_(DEMO_RULES))))
    assert sources == {CategorySource.CORRECTION}


def test_extras_plant_no_flags(client, db):
    plain_headers = register(client, "plain@umd.edu")
    plain = checking_id(client, plain_headers)
    upload(client, plain_headers, plain, FIXTURE)
    apply_labels(db, plain, FIXTURE, LABELS)
    _, demo = seeded(client, db)
    flags = lambda account: db.scalar(select(func.count()).select_from(AnomalyFlag).join(Transaction)  # noqa: E731
                                      .where(Transaction.account_id == account))
    assert flags(demo) == flags(plain)


def test_extras_are_idempotent(client, db):
    _, account_id = seeded(client, db)
    assert add_demo_extras(db, account_id) == (len(DEMO_RULES), 0)
    count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id))
    assert count == 63 + len(DEMO_REVIEW_ROWS)


def test_delete_user_removes_everything_they_own(client, db):
    _, account_id = seeded(client, db)
    assert delete_user(db, "demo@umd.edu") is True
    db.commit()
    assert db.scalar(select(func.count()).select_from(User).where(User.email == "demo@umd.edu")) == 0
    assert db.scalar(select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id)) == 0
    assert db.scalar(select(func.count()).select_from(CategoryCorrection)) == 0
    assert delete_user(db, "demo@umd.edu") is False
