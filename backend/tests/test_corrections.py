"""GET /corrections, PATCH /corrections/{id} and DELETE /corrections/{id}: the saved rules list."""

from sqlalchemy import select

from app.models import Category, CategorySource, Transaction
from tests.agent.helpers import checking_id, upload
from tests.conftest import register
from tests.test_review import CSV, review, setup_user

MORE_STARBUCKS = b"date,description,amount\n2026-11-02,STARBUCKS STORE 99999,-3.95\n"


def remember(client, headers, merchant, category):
    """Resolve a merchant on the Review page with Remember on, which saves a rule."""
    r = client.post("/review/resolve", headers=headers, json={"merchant": merchant, "category": category, "remember": True})
    assert r.status_code == 200, r.text


def rules(client, headers):
    r = client.get("/corrections", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def rule(client, headers, merchant):
    return next(x for x in rules(client, headers) if x["merchant"] == merchant)


def categories(db, merchant, account_id=None):
    """(category name, source) of every transaction from `merchant` (in one account, if given), oldest first."""
    stmt = (
        select(Category.name, Transaction.category_source).select_from(Transaction)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(Transaction.merchant == merchant).order_by(Transaction.date, Transaction.id)
    )
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    rows = db.execute(stmt).all()
    return [(name, source.value if source else None) for name, source in rows]


def queued(client, headers):
    return {g["merchant"] for g in review(client, headers)["groups"]}


def test_list_is_your_rules_a_to_z_with_counts_and_mixed_label(client, db):
    headers, _ = setup_user(client)
    remember(client, headers, "STARBUCKS", "Dining")
    remember(client, headers, "AMAZON", "Shopping")
    assert rules(client, headers) == [
        {"id": rule(client, headers, "AMAZON")["id"], "merchant": "AMAZON", "category": "Shopping",
         "transaction_count": 2, "mixed": True},
        {"id": rule(client, headers, "STARBUCKS")["id"], "merchant": "STARBUCKS", "category": "Dining",
         "transaction_count": 3, "mixed": False},
    ]
    other, _ = setup_user(client, "b@umd.edu")
    assert rules(client, other) == []


def test_empty_and_needs_login(client):
    headers = register(client, "a@umd.edu")
    assert rules(client, headers) == []
    assert client.get("/corrections").status_code == 401


def test_count_leaves_out_the_merchants_rows_in_other_categories(client, db):
    headers, _ = setup_user(client)
    remember(client, headers, "AMAZON", "Shopping")
    refund = db.scalar(select(Transaction).where(Transaction.merchant == "AMAZON", Transaction.amount > 0))
    client.patch(f"/transactions/{refund.id}", headers=headers, json={"category": "Education"})  # a deliberate exception
    assert rule(client, headers, "AMAZON")["transaction_count"] == 1


def test_change_moves_the_rules_rows_and_future_imports(client, db):
    headers, account_id = setup_user(client)
    remember(client, headers, "AMAZON", "Shopping")
    refund = db.scalar(select(Transaction).where(Transaction.merchant == "AMAZON", Transaction.amount > 0))
    client.patch(f"/transactions/{refund.id}", headers=headers, json={"category": "Education"})

    r = client.patch(f"/corrections/{rule(client, headers, 'AMAZON')['id']}", headers=headers,
                     json={"category": "Groceries"})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    assert (r.json()["rule"]["category"], r.json()["rule"]["transaction_count"]) == ("Groceries", 1)
    # the row resolved on the Review page moved; the hand-set exception didn't
    assert categories(db, "AMAZON") == [("Groceries", "correction"), ("Education", "user")]

    upload(client, headers, account_id, b"date,description,amount\n2026-11-03,AMZN Mktp US*9Z9Z9,-8.00\n")
    assert categories(db, "AMAZON")[-1] == ("Groceries", "correction")


def test_remove_keeps_rows_by_default_but_stops_future_imports(client, db):
    headers, account_id = setup_user(client)
    remember(client, headers, "STARBUCKS", "Dining")
    r = client.delete(f"/corrections/{rule(client, headers, 'STARBUCKS')['id']}", headers=headers)
    assert (r.status_code, r.json()) == (200, {"sent_to_review": 0})
    assert rules(client, headers) == []
    assert categories(db, "STARBUCKS") == [("Dining", "user")] * 3

    upload(client, headers, account_id, MORE_STARBUCKS)
    assert categories(db, "STARBUCKS")[-1] == (None, None)  # no rule any more, and no LLM in tests
    assert "STARBUCKS" in queued(client, headers)


def test_remove_can_send_the_rules_rows_back_to_review(client, db):
    headers, _ = setup_user(client)
    remember(client, headers, "STARBUCKS", "Dining")
    remember(client, headers, "AMAZON", "Shopping")
    assert "STARBUCKS" not in queued(client, headers)

    r = client.delete(f"/corrections/{rule(client, headers, 'STARBUCKS')['id']}?send_to_review=true", headers=headers)
    assert r.json() == {"sent_to_review": 3}
    assert categories(db, "STARBUCKS") == [(None, None)] * 3
    assert "STARBUCKS" in queued(client, headers)
    assert categories(db, "AMAZON") == [("Shopping", "user")] * 2  # other rules untouched


def test_another_users_rule_is_404(client, db):
    a, a_account = setup_user(client)
    remember(client, a, "STARBUCKS", "Dining")
    a_rule = rule(client, a, "STARBUCKS")["id"]
    b = register(client, "b@umd.edu")
    upload(client, b, checking_id(client, b), CSV)

    assert client.patch(f"/corrections/{a_rule}", headers=b, json={"category": "Groceries"}).status_code == 404
    assert client.delete(f"/corrections/{a_rule}?send_to_review=true", headers=b).status_code == 404
    assert rule(client, a, "STARBUCKS")["category"] == "Dining"
    assert categories(db, "STARBUCKS", a_account) == [("Dining", "user")] * 3


def test_change_rejects_an_unknown_category(client):
    headers, _ = setup_user(client)
    remember(client, headers, "STARBUCKS", "Dining")
    rule_id = rule(client, headers, "STARBUCKS")["id"]
    assert client.patch(f"/corrections/{rule_id}", headers=headers, json={"category": "Pizza"}).status_code == 422


def test_rows_from_apply_to_others_count_too(client, db):
    headers, _ = setup_user(client)
    first = db.scalar(select(Transaction).where(Transaction.merchant == "STARBUCKS").order_by(Transaction.date))
    client.patch(f"/transactions/{first.id}", headers=headers, json={"category": "Dining", "apply_to_merchant": True})
    sources = [s for _, s in categories(db, "STARBUCKS")]
    assert sources == ["user", "correction", "correction"]
    assert rule(client, headers, "STARBUCKS")["transaction_count"] == 3
    assert db.scalar(select(Transaction.category_source).where(Transaction.id == first.id)) == CategorySource.USER
