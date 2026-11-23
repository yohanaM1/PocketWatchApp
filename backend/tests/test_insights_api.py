"""GET /recurring, GET /recurring/transactions, GET /anomalies and PATCH /anomalies/{id}."""

from datetime import date, timedelta

from app.devdata import apply_labels
from tests.conftest import register
from tests.test_imports import checking_id, upload
from tests.test_insights_store import FIXTURE, LABELS


def setup(client, db, email="a@umd.edu", labels=True):
    """A user with the fixture imported and (by default) labeled. Returns the auth headers."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    assert upload(client, headers, account_id, FIXTURE).status_code == 200
    if labels:
        apply_labels(db, account_id, FIXTURE, LABELS)
        db.commit()
    return headers


def monthly_csv(merchant_desc: str, amounts: list[str], start: date) -> bytes:
    """A CSV with one charge a month."""
    lines = [f"{(start + timedelta(days=30 * i)).isoformat()},{merchant_desc},{a}" for i, a in enumerate(amounts)]
    return ("date,description,amount\n" + "\n".join(lines) + "\n").encode()


def test_recurring_lists_active_series_largest_first(client, db):
    headers = setup(client, db)
    page = client.get("/recurring", headers=headers).json()
    assert page["as_of"] == "2026-10-30"
    assert (page["monthly_total"], page["active_count"]) == ("-1006.96", 8)
    merchants = [s["merchant"] for s in page["series"]]
    assert merchants[0] == "LANDMARK COLLEGE PARK RENT" and merchants[-1] == "APPLE"
    netflix = next(s for s in page["series"] if s["merchant"] == "NETFLIX")
    assert netflix == {
        "key": "NETFLIX:2026-09-23:7.99", "merchant": "NETFLIX", "category": "Subscriptions", "amount": "-7.99",
        "previous_amount": None, "amount_changed_on": None, "interval_days": 28, "first_date": "2026-09-23",
        "last_date": "2026-10-21", "next_expected": "2026-11-18", "charge_count": 2, "active": True,
    }


def test_stopped_series_come_last_with_no_next_date(client, db):
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, monthly_csv("PLANET FITNESS CLUB #1234", ["-10.00"] * 3, date(2026, 4, 17)))
    upload(client, headers, account_id, monthly_csv("NETFLIX.COM 1234 CA", ["-7.99"] * 3 + ["-8.99"] * 3,
                                                    date(2026, 4, 21)))
    page = client.get("/recurring", headers=headers).json()
    assert [(s["merchant"], s["active"]) for s in page["series"]] == [("NETFLIX", True), ("PLANET FITNESS CLUB", False)]
    gym = page["series"][1]
    assert gym["next_expected"] is None
    netflix = page["series"][0]
    assert (netflix["amount"], netflix["previous_amount"]) == ("-8.99", "-7.99")
    assert (page["monthly_total"], page["active_count"]) == ("-8.99", 1)


def test_recurring_empty(client):
    headers = register(client, "a@umd.edu")
    assert client.get("/recurring", headers=headers).json() == {
        "as_of": None, "monthly_total": "0.00", "active_count": 0, "series": []}


def test_series_transactions_by_key(client, db):
    headers = setup(client, db)
    r = client.get("/recurring/transactions", headers=headers, params={"key": "NETFLIX:2026-09-23:7.99"})
    assert r.status_code == 200
    assert [(t["date"], t["amount"]) for t in r.json()] == [("2026-10-21", "-7.99"), ("2026-09-23", "-7.99")]
    assert client.get("/recurring/transactions", headers=headers, params={"key": "NOPE:2026-01-01:1.00"}).status_code == 404


def test_flags_list_newest_first_with_their_transactions(client, db):
    headers = setup(client, db)
    page = client.get("/anomalies", headers=headers).json()
    assert page["open_count"] == 3
    assert [(f["kind"], f["transaction"]["merchant"], f["transaction"]["amount"]) for f in page["items"]] == [
        ("unfamiliar_merchant", "XJ4HOLDINGS", "-225.56"),
        ("duplicate", "UBER", "-25.14"),
        ("large_charge", "AMAZON", "-403.43"),
    ]
    assert all(f["status"] == "open" for f in page["items"])
    assert page["items"][2]["reason"] == "About 8× your usual Shopping charge"


def test_dismiss_confirm_and_reopen(client, db):
    headers = setup(client, db)
    first, second, _ = client.get("/anomalies", headers=headers).json()["items"]

    r = client.patch(f"/anomalies/{first['id']}", headers=headers, json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    client.patch(f"/anomalies/{second['id']}", headers=headers, json={"status": "confirmed"})

    assert client.get("/anomalies", headers=headers).json()["open_count"] == 1
    assert [f["id"] for f in client.get("/anomalies", headers=headers, params={"status": "dismissed"}).json()["items"]] \
        == [first["id"]]
    assert [f["id"] for f in client.get("/anomalies", headers=headers, params={"status": "confirmed"}).json()["items"]] \
        == [second["id"]]
    assert len(client.get("/anomalies", headers=headers, params={"status": "all"}).json()["items"]) == 3

    client.patch(f"/anomalies/{first['id']}", headers=headers, json={"status": "open"})
    assert client.get("/anomalies", headers=headers).json()["open_count"] == 2


def test_bad_status_values_are_rejected(client, db):
    headers = setup(client, db)
    flag = client.get("/anomalies", headers=headers).json()["items"][0]
    assert client.patch(f"/anomalies/{flag['id']}", headers=headers, json={"status": "deleted"}).status_code == 422
    assert client.get("/anomalies", headers=headers, params={"status": "deleted"}).status_code == 422


def test_users_never_see_or_change_each_others_series_and_flags(client, db):
    headers_a = setup(client, db, "a@umd.edu")
    headers_b = register(client, "b@umd.edu")
    flag = client.get("/anomalies", headers=headers_a).json()["items"][0]

    assert client.get("/recurring", headers=headers_b).json()["series"] == []
    assert client.get("/anomalies", headers=headers_b).json() == {"items": [], "open_count": 0}
    assert client.get("/recurring/transactions", headers=headers_b,
                      params={"key": "NETFLIX:2026-09-23:7.99"}).status_code == 404
    assert client.patch(f"/anomalies/{flag['id']}", headers=headers_b, json={"status": "dismissed"}).status_code == 404
    assert client.get("/anomalies", headers=headers_a).json()["open_count"] == 3


def test_endpoints_need_a_login(client):
    for path in ("/recurring", "/recurring/transactions?key=x", "/anomalies"):
        assert client.get(path).status_code == 401
    assert client.patch("/anomalies/1", json={"status": "dismissed"}).status_code == 401
