from sqlalchemy import select, update

from app import config
from app.models import Category, CategoryCorrection, CategorySource, Transaction
from tests.agent.helpers import checking_id, upload
from tests.conftest import register

CSV = (
    b"date,description,amount\n"
    b"2026-09-03,SQ *STARBUCKS #2411 HYATTSVILLE MD,-6.45\n"
    b"2026-09-10,STARBUCKS STORE 02606,-5.10\n"
    b"2026-10-01,STARBUCKS 12345 COLLEGE PARK MD,-4.25\n"
    b"2026-10-02,AMZN Mktp US*2K4L1,-30.00\n"
    b"2026-10-05,GIANT 8451 HYATTSVILLE MD,-47.73\n"
    b"2026-10-06,AMAZON.COM*RT3 AMZN.COM/BILL WA,12.00\n"
)


# Helper: a user with the CSV imported into Checking
def setup_user(client, email="a@umd.edu", content=CSV):
    """Register and import `content`; returns (headers, account_id)."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, content)
    return headers, account_id


# Helper: GET /review
def review(client, headers, **params):
    """The review page JSON."""
    r = client.get("/review", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# Helper: a group by merchant
def group(body, merchant):
    """The group for `merchant` in a review page."""
    return next(g for g in body["groups"] if g["merchant"] == merchant)


# Helper: the user's transaction ids for a merchant, oldest first
def ids_for(client, headers, merchant):
    """Ids of `merchant`'s transactions, oldest first."""
    items = client.get("/transactions", headers=headers, params={"merchant": merchant}).json()["items"]
    return [t["id"] for t in reversed(items)]


# Helper: store a categorization the way the LLM pipeline (or another source) would
def set_category(db, txn_id, category, source=CategorySource.LLM, confidence=None):
    """Set a transaction's category, source and confidence directly."""
    category_id = db.scalar(select(Category.id).where(Category.name == category))
    db.execute(update(Transaction).where(Transaction.id == txn_id)
               .values(category_id=category_id, category_source=source, category_confidence=confidence))
    db.flush()


# Helper: queue size from the transactions filter
def queued(client, headers):
    """GET /transactions?review=true total, as the nav badge reads it."""
    return client.get("/transactions", headers=headers, params={"review": "true", "limit": 1}).json()["total"]


# What's in the queue
def test_queue_holds_uncategorized_and_low_confidence_guesses_only(client, db):
    """Uncategorized, LLM below the threshold or without confidence: queued. Confident LLM, user and correction: not."""
    headers, _ = setup_user(client)
    sb = ids_for(client, headers, "STARBUCKS")
    amazon = ids_for(client, headers, "AMAZON")
    giant = ids_for(client, headers, "GIANT")
    set_category(db, sb[0], "Dining", confidence=0.5)  # low: queued
    set_category(db, sb[1], "Dining", confidence=None)  # no confidence counts as low: queued
    set_category(db, sb[2], "Dining", confidence=config.REVIEW_CONFIDENCE_THRESHOLD)  # at the threshold: accepted
    set_category(db, amazon[0], "Shopping", confidence=0.95)  # confident: accepted
    set_category(db, amazon[1], "Shopping", source=CategorySource.USER, confidence=1.0)
    set_category(db, giant[0], "Groceries", source=CategorySource.CORRECTION, confidence=1.0)

    body = review(client, headers)
    assert [(g["merchant"], g["count"]) for g in body["groups"]] == [("STARBUCKS", 2)]
    assert body["total_transactions"] == 2 == queued(client, headers)


# The threshold is a setting
def test_threshold_comes_from_config(client, db, monkeypatch):
    """Lowering REVIEW_CONFIDENCE_THRESHOLD accepts guesses that were queued before."""
    headers, _ = setup_user(client)
    set_category(db, ids_for(client, headers, "GIANT")[0], "Groceries", confidence=0.5)
    assert "GIANT" in [g["merchant"] for g in review(client, headers)["groups"]]
    monkeypatch.setattr(config, "REVIEW_CONFIDENCE_THRESHOLD", 0.4)
    assert "GIANT" not in [g["merchant"] for g in review(client, headers)["groups"]]


# Group shape
def test_groups_summarize_each_merchant(client):
    """Biggest groups first; counts, money in/out, date range, and up to 3 newest examples."""
    headers, _ = setup_user(client)
    body = review(client, headers)
    assert [(g["merchant"], g["count"]) for g in body["groups"]] == [("STARBUCKS", 3), ("AMAZON", 2), ("GIANT", 1)]
    assert (body["total_groups"], body["total_transactions"]) == (3, 6)

    sb = group(body, "STARBUCKS")
    assert (sb["reason"], sb["guesses"], sb["suggestion"]) == ("uncategorized", [], None)
    assert (sb["money_out"], sb["money_in"]) == ("15.80", "0.00")
    assert (sb["first_date"], sb["last_date"]) == ("2026-09-03", "2026-10-01")
    assert [e["date"] for e in sb["examples"]] == ["2026-10-01", "2026-09-10", "2026-09-03"]
    assert (sb["mixed"], sb["mixed_reason"]) == (False, None)

    amazon = group(body, "AMAZON")
    assert (amazon["money_out"], amazon["money_in"]) == ("30.00", "12.00")


# Paging
def test_paging_is_over_groups(client):
    """limit/offset select groups; totals still cover everything."""
    headers, _ = setup_user(client)
    body = review(client, headers, limit=1, offset=1)
    assert [g["merchant"] for g in body["groups"]] == ["AMAZON"]
    assert body["total_groups"] == 3


# Guesses
def test_guesses_are_listed_per_category_with_their_lowest_confidence(client, db):
    """Most common guess first and used as the suggestion; disagreeing guesses make the group mixed."""
    headers, _ = setup_user(client)
    sb = ids_for(client, headers, "STARBUCKS")
    set_category(db, sb[0], "Dining", confidence=0.6)
    set_category(db, sb[1], "Dining", confidence=0.5)
    set_category(db, sb[2], "Groceries", confidence=0.4)

    g = group(review(client, headers), "STARBUCKS")
    assert g["reason"] == "low_confidence"
    assert g["guesses"] == [{"category": "Dining", "count": 2, "confidence": 0.5},
                            {"category": "Groceries", "count": 1, "confidence": 0.4}]
    assert g["suggestion"] == "Dining"
    assert (g["mixed"], g["mixed_reason"]) == (True, "guesses_differ")


# Mixed merchants
def test_known_multi_purpose_merchant_is_mixed(client):
    """AMAZON is on MIXED_MERCHANTS even with no guesses at all."""
    headers, _ = setup_user(client)
    g = group(review(client, headers), "AMAZON")
    assert (g["mixed"], g["mixed_reason"]) == (True, "known_mixed")


def test_history_of_different_hand_set_categories_makes_a_merchant_mixed(client):
    """Setting two Starbucks rows by hand to different categories marks the rest of Starbucks as mixed."""
    headers, _ = setup_user(client)
    sb = ids_for(client, headers, "STARBUCKS")
    client.patch(f"/transactions/{sb[0]}", headers=headers, json={"category": "Dining"})
    client.patch(f"/transactions/{sb[1]}", headers=headers, json={"category": "Groceries"})
    g = group(review(client, headers), "STARBUCKS")
    assert (g["count"], g["mixed_reason"]) == (1, "history_differs")


def test_another_users_history_never_makes_your_merchant_mixed(client):
    """history_differs only reads your own hand-set rows."""
    other, _ = setup_user(client, "b@umd.edu")
    sb = ids_for(client, other, "STARBUCKS")
    client.patch(f"/transactions/{sb[0]}", headers=other, json={"category": "Dining"})
    client.patch(f"/transactions/{sb[1]}", headers=other, json={"category": "Groceries"})
    headers, _ = setup_user(client)
    assert group(review(client, headers), "STARBUCKS")["mixed"] is False


# Resolving with remember
def test_resolve_with_remember_saves_a_correction_and_reaches_future_imports(client, db):
    """Queued rows become hand-set; the correction recategorizes other non-hand-set rows and the next import."""
    headers, account_id = setup_user(client)
    sb = ids_for(client, headers, "STARBUCKS")
    set_category(db, sb[0], "Groceries", confidence=0.95)  # confident guess, not queued, but not set by hand either
    client.patch(f"/transactions/{sb[1]}", headers=headers, json={"category": "Shopping"})  # set by hand: never touched

    r = client.post("/review/resolve", headers=headers, json={"merchant": "STARBUCKS", "category": "Dining"})
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "also_updated": 1}

    db.expire_all()
    rows = {t.id: t for t in db.scalars(select(Transaction).where(Transaction.id.in_(sb)))}
    assert (rows[sb[2]].category_source, rows[sb[2]].category_confidence) == (CategorySource.USER, 1.0)
    assert rows[sb[0]].category_source == CategorySource.CORRECTION
    assert rows[sb[1]].category_source == CategorySource.USER  # still Shopping
    assert db.scalar(select(CategoryCorrection.merchant)) == "STARBUCKS"
    assert "STARBUCKS" not in [g["merchant"] for g in review(client, headers)["groups"]]

    upload(client, headers, account_id, b"date,description,amount\n2026-10-20,STARBUCKS 999 GREENBELT MD,-3.00\n")
    assert "STARBUCKS" not in [g["merchant"] for g in review(client, headers)["groups"]]


def test_resolve_without_remember_saves_nothing_for_the_future(client, db):
    """remember=false: only the queued rows change; no correction, so the next import is queued again."""
    headers, account_id = setup_user(client)
    r = client.post("/review/resolve", headers=headers,
                    json={"merchant": "AMAZON", "category": "Shopping", "remember": False})
    assert r.json() == {"updated": 2, "also_updated": 0}
    assert db.scalar(select(CategoryCorrection.id)) is None
    upload(client, headers, account_id, b"date,description,amount\n2026-10-21,AMZN Mktp US*9Z,-8.00\n")
    assert group(review(client, headers), "AMAZON")["count"] == 1


# Errors and isolation
def test_resolve_errors(client):
    """404 when nothing from the merchant is queued (including after resolving it), 422 for an unknown category."""
    headers, _ = setup_user(client)
    assert client.post("/review/resolve", headers=headers,
                       json={"merchant": "NOPE", "category": "Dining"}).status_code == 404
    assert client.post("/review/resolve", headers=headers,
                       json={"merchant": "GIANT", "category": "Snacks"}).status_code == 422
    assert client.post("/review/resolve", headers=headers,
                       json={"merchant": "GIANT", "category": "Groceries"}).status_code == 200
    assert client.post("/review/resolve", headers=headers,
                       json={"merchant": "GIANT", "category": "Groceries"}).status_code == 404


def test_users_never_see_or_resolve_each_others_queue(client):
    """Another user's queue is invisible, and resolving a merchant only reaches your own rows."""
    a, _ = setup_user(client, "a@umd.edu")
    b = register(client, "b@umd.edu")
    assert review(client, b)["groups"] == []
    assert client.post("/review/resolve", headers=b, json={"merchant": "GIANT", "category": "Dining"}).status_code == 404
    assert group(review(client, a), "GIANT")["count"] == 1


# New /transactions filters
def test_transactions_merchant_and_review_filters(client, db):
    """merchant= is an exact match; review=true keeps only queued rows; confidence is in the response."""
    headers, _ = setup_user(client)
    sb = ids_for(client, headers, "STARBUCKS")
    set_category(db, sb[0], "Dining", confidence=0.95)
    body = client.get("/transactions", headers=headers, params={"merchant": "STARBUCKS", "review": "true"}).json()
    assert body["total"] == 2
    assert all(t["merchant"] == "STARBUCKS" and t["category_confidence"] is None for t in body["items"])
    confident = client.get("/transactions", headers=headers, params={"merchant": "STARBUCKS"}).json()["items"][-1]
    assert confident["category_confidence"] == 0.95
