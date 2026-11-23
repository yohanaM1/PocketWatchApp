from tests.agent.helpers import FIXTURE_CSV, checking_id, upload
from tests.conftest import register

CSV = (
    b"date,description,amount\n"
    b"2026-09-03,CHIPOTLE MEXICAN GRILL COLLEGE PARK MD,-11.38\n"
    b"2026-09-20,CHIPOTLE MEXICAN GRILL COLLEGE PARK MD,-12.10\n"
    b"2026-10-02,CHIPOTLE MEXICAN GRILL COLLEGE PARK MD,-9.75\n"
    b"2026-10-05,GIANT 4269 WASHINGTON DC,-47.73\n"
)


# Helper: a user with the small CSV imported into Checking
def setup_user(client, email="a@umd.edu", content=CSV):
    """Register, import `content` into Checking; returns (headers, account_id)."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, content)
    return headers, account_id


# Helper: list transactions with query params
def listing(client, headers, **params):
    """GET /transactions and return the JSON body."""
    r = client.get("/transactions", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# Helper: the ids of the Chipotle rows, newest first
def chipotle_ids(client, headers):
    """Ids of the three Chipotle transactions, newest first."""
    return [t["id"] for t in listing(client, headers)["items"] if "CHIPOTLE" in t["description"]]


# Listing order and shape
def test_list_newest_first_with_strings(client):
    """Newest first; amounts are 2-decimal strings; imports start uncategorized."""
    headers, account_id = setup_user(client)
    body = listing(client, headers)
    assert body["total"] == 4
    assert [t["date"] for t in body["items"]] == ["2026-10-05", "2026-10-02", "2026-09-20", "2026-09-03"]
    first = body["items"][0]
    assert first["amount"] == "-47.73" and first["account_id"] == account_id and first["account_name"] == "Checking"
    assert first["category"] is None and first["category_source"] is None


# Totals for the stat tiles
def test_totals_cover_every_page_of_the_filter(client):
    """money_out / money_in / uncategorized_count sum the whole filtered set, not just the page."""
    headers, _ = setup_user(client, content=CSV + b"2026-10-06,UMD PAYROLL DIR DEP,400.21\n")
    body = listing(client, headers, limit=1)
    assert len(body["items"]) == 1
    assert (body["money_out"], body["money_in"], body["uncategorized_count"]) == ("80.96", "400.21", 5)
    sept = listing(client, headers, month="2026-09")
    assert (sept["money_out"], sept["money_in"]) == ("23.48", "0.00")
    client.patch(f"/transactions/{chipotle_ids(client, headers)[0]}", headers=headers, json={"category": "Dining"})
    assert listing(client, headers)["uncategorized_count"] == 4
    assert listing(client, headers, month="2030-01")["money_out"] == "0.00"


# Filters
def test_filters(client):
    """month, account_id and category (including uncategorized) narrow the list; total counts every page."""
    headers, account_id = setup_user(client)
    assert listing(client, headers, month="2026-09")["total"] == 2
    assert listing(client, headers, month="2026-11")["total"] == 0
    assert listing(client, headers, account_id=account_id)["total"] == 4
    assert listing(client, headers, category="uncategorized")["total"] == 4
    assert listing(client, headers, category="Dining")["total"] == 0
    page = listing(client, headers, limit=3, offset=3)
    assert page["total"] == 4 and len(page["items"]) == 1


# Filter validation
def test_filter_validation(client):
    """A bad month or unknown category is 422."""
    headers, _ = setup_user(client)
    assert client.get("/transactions", headers=headers, params={"month": "2026-13"}).status_code == 422
    assert client.get("/transactions", headers=headers, params={"category": "Pizza"}).status_code == 422


# Per-user scoping
def test_users_only_see_their_own_transactions(client):
    """Another user's transactions, account and PATCH target are all invisible (404, not 403)."""
    alice, alice_account = setup_user(client, "alice@umd.edu")
    bob = register(client, "bob@umd.edu")
    assert listing(client, bob) == {"items": [], "total": 0, "money_out": "0.00", "money_in": "0.00",
                                    "uncategorized_count": 0, "review_count": 0}
    assert client.get("/transactions", headers=bob, params={"account_id": alice_account}).status_code == 404
    txn_id = chipotle_ids(client, alice)[0]
    r = client.patch(f"/transactions/{txn_id}", headers=bob, json={"category": "Dining"})
    assert r.status_code == 404


# Single-row category edit
def test_patch_one_row_reports_same_merchant_count(client, db):
    """Without apply_to_merchant only that row changes, and the other same-merchant rows are counted."""
    headers, _ = setup_user(client)
    newest, *rest = chipotle_ids(client, headers)
    r = client.patch(f"/transactions/{newest}", headers=headers, json={"category": "Dining"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transaction"]["category"] == "Dining" and body["transaction"]["category_source"] == "user"
    assert body["also_updated"] == 0 and body["same_merchant_count"] == 2
    assert listing(client, headers, category="Dining")["total"] == 1
    assert client.get("/categories", headers=headers).json()[0] == "Dining"


# Merchant-wide category edit
def test_apply_to_merchant_skips_hand_set_rows(client):
    """apply_to_merchant updates other same-merchant rows as corrections, but never a row set by hand."""
    headers, _ = setup_user(client)
    newest, middle, oldest = chipotle_ids(client, headers)
    client.patch(f"/transactions/{oldest}", headers=headers, json={"category": "Groceries"})  # set by hand
    r = client.patch(f"/transactions/{newest}", headers=headers, json={"category": "Dining", "apply_to_merchant": True})
    body = r.json()
    assert body["also_updated"] == 1 and body["same_merchant_count"] == 0
    by_id = {t["id"]: t for t in listing(client, headers)["items"]}
    assert (by_id[middle]["category"], by_id[middle]["category_source"]) == ("Dining", "correction")
    assert (by_id[oldest]["category"], by_id[oldest]["category_source"]) == ("Groceries", "user")


# Category validation
def test_patch_unknown_category(client):
    """An unknown category name is 422."""
    headers, _ = setup_user(client)
    txn_id = chipotle_ids(client, headers)[0]
    assert client.patch(f"/transactions/{txn_id}", headers=headers, json={"category": "Pizza"}).status_code == 422


# Corrections are reused on import
def test_saved_correction_applies_to_future_imports(client):
    """After an apply_to_merchant edit, a new import categorizes that merchant automatically."""
    headers, account_id = setup_user(client)
    client.patch(f"/transactions/{chipotle_ids(client, headers)[0]}", headers=headers,
                 json={"category": "Dining", "apply_to_merchant": True})
    upload(client, headers, account_id, b"date,description,amount\n2026-10-20,CHIPOTLE MEXICAN GRILL COLLEGE PARK MD,-8.40\n")
    new = listing(client, headers, month="2026-10")["items"][0]
    assert new["date"] == "2026-10-20"
    assert (new["category"], new["category_source"]) == ("Dining", "correction")


# Corrections are per user
def test_corrections_do_not_leak_between_users(client):
    """Alice's merchant correction never categorizes Bob's imports."""
    alice, _ = setup_user(client, "alice@umd.edu")
    client.patch(f"/transactions/{chipotle_ids(client, alice)[0]}", headers=alice,
                 json={"category": "Dining", "apply_to_merchant": True})
    bob, _ = setup_user(client, "bob@umd.edu", FIXTURE_CSV)
    chipotle = [t for t in listing(client, bob, limit=200)["items"] if "CHIPOTLE" in t["description"]]
    assert chipotle and all(t["category"] is None for t in chipotle)
