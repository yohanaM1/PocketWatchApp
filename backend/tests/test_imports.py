from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app import config
from app.models import Transaction
from tests.conftest import register

FIXTURE = (Path(__file__).resolve().parents[2] / "fixtures" / "sample_transactions.csv").read_bytes()


# Helper: id of the user's default account
def checking_id(client, headers) -> int:
    """Return the id of the user's default Checking account."""
    return client.get("/accounts", headers=headers).json()[0]["id"]


# Helper: upload a CSV to an account
def upload(client, headers, account_id, content: bytes, filename="transactions.csv"):
    """POST a CSV file to the import endpoint for an account."""
    return client.post(
        f"/accounts/{account_id}/imports", headers=headers, files={"file": (filename, content, "text/csv")}
    )


# Re-importing is a no-op
def test_import_fixture_then_reimport_is_noop(client, db):
    """Importing the fixture adds 63 rows; importing it again adds none."""
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)

    first = upload(client, headers, account_id, FIXTURE)
    assert first.status_code == 200
    assert first.json() == {"total_rows": 63, "imported": 63, "skipped_duplicates": 0,
                            "categorized": 0, "needs_review": 63}  # no LLM in tests

    again = upload(client, headers, account_id, FIXTURE)
    assert again.json() == {"total_rows": 63, "imported": 0, "skipped_duplicates": 63,
                            "categorized": 0, "needs_review": 0}

    count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id))
    assert count == 63


# Stored values after import
def test_imported_values_and_merchant(client, db):
    """Imported amounts are exact, merchant names are cleaned up, and categories start empty."""
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, FIXTURE)

    total_out = db.scalar(
        select(func.sum(Transaction.amount)).where(Transaction.account_id == account_id, Transaction.amount < 0)
    )
    assert total_out == Decimal("-3499.70")  # matches fixtures/README.md

    amazon = db.scalar(select(Transaction).where(Transaction.amount == Decimal("-403.43")))
    assert amazon.merchant == "AMAZON"
    assert amazon.raw_description.startswith("AMAZON.COM*")
    assert amazon.category_id is None  # categorization comes later


# Real double charges are kept
def test_planted_duplicate_charge_is_kept(client, db):
    """The fixture's planted same-day double charge is stored twice, so anomaly detection can find it."""
    headers = register(client, "a@umd.edu")
    upload(client, headers, checking_id(client, headers), FIXTURE)
    uber = db.scalars(select(Transaction).where(Transaction.amount == Decimal("-25.14"))).all()
    assert len(uber) == 2


# Overlapping statements
def test_overlapping_file_only_adds_new_rows(client):
    """A statement overlapping a previous import only adds the rows not seen before."""
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, b"date,description,amount\n2026-09-01,A,-1\n2026-09-02,B,-2\n")
    r = upload(client, headers, account_id, b"date,description,amount\n2026-09-02,B,-2\n2026-09-03,C,-3\n")
    assert r.json() == {"total_rows": 2, "imported": 1, "skipped_duplicates": 1,
                        "categorized": 0, "needs_review": 1}


# Dedup is per account
def test_same_file_into_two_accounts_imports_twice(client):
    """Duplicate skipping is per account, so the same file can go into two accounts."""
    headers = register(client, "a@umd.edu")
    card = client.post("/accounts", json={"name": "Card", "type": "credit"}, headers=headers).json()["id"]
    assert upload(client, headers, checking_id(client, headers), FIXTURE).json()["imported"] == 63
    assert upload(client, headers, card, FIXTURE).json()["imported"] == 63


# All-or-nothing import
def test_invalid_file_imports_nothing(client, db):
    """One bad row returns 422 with the line and reason, and no rows are saved."""
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)
    r = upload(client, headers, account_id, b"date,description,amount\n2026-09-01,A,-1\n2026-09-02,B,oops\n")
    assert r.status_code == 422
    assert r.json()["detail"]["errors"] == [{"line": 3, "message": "Invalid amount 'oops'"}]
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0


# File type and size limits
def test_rejects_non_csv_and_large_files(client, monkeypatch):
    """Non-.csv files get 400 and files over the size limit get 413."""
    headers = register(client, "a@umd.edu")
    account_id = checking_id(client, headers)
    assert upload(client, headers, account_id, FIXTURE, filename="statement.pdf").status_code == 400

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    assert upload(client, headers, account_id, FIXTURE).status_code == 413


# Auth required
def test_requires_login(client):
    """Importing without a token gets 401."""
    assert upload(client, {}, 1, FIXTURE).status_code == 401


# Per-user account scoping on import
def test_cannot_import_into_someone_elses_account(client, db):
    """Importing into another user's account looks the same as a missing account (404) and saves nothing."""
    alice = register(client, "alice@umd.edu")
    bob = register(client, "bob@umd.edu")
    alice_account = checking_id(client, alice)

    r = upload(client, bob, alice_account, FIXTURE)
    assert r.status_code == 404
    assert upload(client, bob, 999_999, FIXTURE).status_code == 404  # same response as a missing account
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0
