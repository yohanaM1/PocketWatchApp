"""refresh_insights: storing series and flags, and when it runs (import, category changes, labels, backfill)."""

from pathlib import Path

from sqlalchemy import select

from app.devdata import apply_labels
from app.models import AnomalyFlag, AnomalyKind, AnomalyStatus, RecurringSeries, Transaction
from app.services import insights
from app.services.insights import refresh_insights
from tests.conftest import register
from tests.test_imports import checking_id, upload

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
FIXTURE = (FIXTURES / "sample_transactions.csv").read_bytes()
LABELS = (FIXTURES / "sample_labels.csv").read_text()


def setup(client, email="a@umd.edu"):
    """A user with the fixture imported (no LLM, so every row is uncategorized). Returns (headers, account id)."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    assert upload(client, headers, account_id, FIXTURE).status_code == 200
    return headers, account_id


def user_id(client, headers) -> int:
    """The logged-in user's id."""
    return client.get("/auth/me", headers=headers).json()["id"]


def series(db, uid):
    """The user's stored series, by key."""
    return {s.key: s for s in db.scalars(select(RecurringSeries).where(RecurringSeries.user_id == uid))}


def flags(db, account_id):
    """The account's flags as {(merchant, kind): flag}."""
    rows = db.execute(select(Transaction.merchant, AnomalyFlag)
                      .join(Transaction, Transaction.id == AnomalyFlag.transaction_id)
                      .where(Transaction.account_id == account_id)).all()
    return {(m, f.kind): f for m, f in rows}


def test_import_stores_series_links_charges_and_flags(client, db):
    headers, account_id = setup(client)
    uid = user_id(client, headers)

    stored = series(db, uid)
    assert len(stored) == 8 and all(s.active for s in stored.values())
    netflix = stored["NETFLIX:2026-09-23:7.99"]
    assert (netflix.charge_count, str(netflix.typical_amount)) == (2, "-7.99")
    linked = db.scalars(select(Transaction.merchant).where(Transaction.recurring_series_id == netflix.id)).all()
    assert linked == ["NETFLIX", "NETFLIX"]

    # Uncategorized after a no-LLM import, so there's no category to compare the Amazon charge with yet.
    assert set(flags(db, account_id)) == {("UBER", AnomalyKind.DUPLICATE), ("XJ4HOLDINGS", AnomalyKind.UNFAMILIAR_MERCHANT)}


def test_labels_add_the_large_charge_flag(client, db):
    _, account_id = setup(client)
    apply_labels(db, account_id, FIXTURE, LABELS)
    found = flags(db, account_id)
    assert ("AMAZON", AnomalyKind.LARGE_CHARGE) in found
    assert len(found) == 3


def test_refresh_is_idempotent_and_keeps_keys(client, db):
    headers, account_id = setup(client)
    uid = user_id(client, headers)
    before_keys, before_flags = set(series(db, uid)), {(k, f.id) for k, f in flags(db, account_id).items()}

    assert refresh_insights(db, uid) == {"series": 8, "new_flags": 0}
    assert set(series(db, uid)) == before_keys
    assert {(k, f.id) for k, f in flags(db, account_id).items()} == before_flags


def test_reimport_keeps_a_dismissed_flag_dismissed(client, db):
    headers, account_id = setup(client)
    flag = flags(db, account_id)[("UBER", AnomalyKind.DUPLICATE)]
    flag.status = AnomalyStatus.DISMISSED
    db.commit()

    upload(client, headers, account_id, FIXTURE)
    db.expire_all()
    assert flags(db, account_id)[("UBER", AnomalyKind.DUPLICATE)].status is AnomalyStatus.DISMISSED


def test_unfamiliar_merchant_keeps_its_flag_after_a_second_charge(client, db):
    headers, account_id = setup(client)
    upload(client, headers, account_id, b"date,description,amount\n2026-10-29,PAYPAL *XJ4HOLDINGS 4029357733,-99.00\n")
    db.expire_all()
    flag = flags(db, account_id)[("XJ4HOLDINGS", AnomalyKind.UNFAMILIAR_MERCHANT)]
    assert flag.status is AnomalyStatus.OPEN


def test_category_change_reruns_detection(client, db):
    headers, account_id = setup(client)
    amazon = db.scalars(select(Transaction).where(Transaction.account_id == account_id,
                                                  Transaction.merchant == "AMAZON")).all()
    others = db.scalars(select(Transaction.id).where(Transaction.merchant.in_(["WALMART"]))).all()
    for tid in others:
        assert client.patch(f"/transactions/{tid}", headers=headers, json={"category": "Shopping"}).status_code == 200
    small = next(t for t in amazon if str(t.amount) == "-62.09")
    client.patch(f"/transactions/{small.id}", headers=headers, json={"category": "Shopping"})
    assert ("AMAZON", AnomalyKind.LARGE_CHARGE) not in flags(db, account_id)  # the big one isn't categorized yet

    big = next(t for t in amazon if str(t.amount) == "-403.43")
    client.patch(f"/transactions/{big.id}", headers=headers, json={"category": "Shopping"})
    assert ("AMAZON", AnomalyKind.LARGE_CHARGE) in flags(db, account_id)


def test_review_resolve_reruns_detection(client, db):
    headers, account_id = setup(client)
    for merchant in ("WALMART", "AMAZON"):
        r = client.post("/review/resolve", headers=headers,
                        json={"merchant": merchant, "category": "Shopping", "remember": False})
        assert r.status_code == 200
    assert ("AMAZON", AnomalyKind.LARGE_CHARGE) in flags(db, account_id)


def test_a_detection_error_never_fails_the_import(client, db, monkeypatch, caplog):
    def boom(rows):
        raise RuntimeError("bug in detection")
    monkeypatch.setattr(insights, "detect_recurring", boom)
    headers = register(client, "b@umd.edu")
    r = upload(client, headers, checking_id(client, headers), FIXTURE)
    assert r.status_code == 200 and r.json()["imported"] == 63
    assert "refresh_insights failed" in caplog.text
    assert db.scalar(select(Transaction.id).where(Transaction.merchant == "NETFLIX")) is not None  # rows kept


def test_users_never_share_series_or_flags(client, db):
    headers_a, account_a = setup(client, "a@umd.edu")
    headers_b = register(client, "b@umd.edu")
    upload(client, headers_b, checking_id(client, headers_b),
           b"date,description,amount\n2026-09-05,NETFLIX.COM 1234 CA,-7.99\n")
    assert series(db, user_id(client, headers_b)) == {}  # one charge: a's Netflix charges don't count
    assert len(series(db, user_id(client, headers_a))) == 8
