"""Applies fixtures/sample_labels.csv to imported fixture rows (seed script and tests), plus the seed script's demo extras."""

import csv
import io
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Category, CategorySource, Transaction
from app.services.csv_import import import_hashes, parse_csv
from app.services.insights import refresh_insights


def apply_labels(db: Session, account_id: int, csv_bytes: bytes, labels_csv: str,
                 *, with_merchants: bool = False) -> int:
    """Set category_id (and optionally merchant) on imported fixture rows, matched by import hash.

    Then re-runs recurring and anomaly detection for the account's owner, since the large-charge rule uses categories.
    """
    rows = parse_csv(csv_bytes)
    hashes = import_hashes(rows)
    names = dict(db.execute(select(Category.name, Category.id)).all())
    updated = 0
    for label in csv.DictReader(io.StringIO(labels_csv)):
        i = int(label["row"]) - 1
        parsed = rows[i]
        # guard against a regenerated fixture whose rows no longer line up with the labels
        if (date.fromisoformat(label["date"]), Decimal(label["amount"]), label["description"]) != \
                (parsed.date, parsed.amount, parsed.description):
            raise ValueError(f"label row {label['row']} doesn't match the transactions file")
        t = db.scalar(select(Transaction).where(Transaction.account_id == account_id,
                                                Transaction.import_hash == hashes[i]))
        if t is None:
            raise ValueError(f"label row {label['row']} not found in account {account_id}")
        t.category_id = names[label["category"]]
        t.category_source, t.category_confidence = CategorySource.USER, 1.0
        if with_merchants:
            t.merchant = label["merchant"]
        updated += 1
    db.flush()
    refresh_insights(db, db.scalar(select(Account.user_id).where(Account.id == account_id)))
    return updated


# Demo extras: what the seed script adds on top of the labeled fixture, so the demo account shows the review queue
# and saved rules. Nothing here changes a spending total in fixtures/README.md: the rules only re-mark rows already in
# their category, and transactions waiting in review aren't counted in spending.
DEMO_RULES = {"STARBUCKS": "Dining", "UMD DINING": "Dining", "AMAZON": "Shopping"}  # AMAZON shows the mixed label

# October rows left for review: (date, description, amount, LLM guess or None, confidence). All under $75 and from
# new merchants, so they plant no flags; the Chipotle pair is 10 days apart, so it isn't a recurring series.
DEMO_REVIEW_ROWS = [
    ("2026-10-08", "CHIPOTLE 2291 COLLEGE PARK MD", "-11.38", "Dining", 0.55),
    ("2026-10-18", "CHIPOTLE ONLINE 7712", "-9.85", "Dining", 0.60),
    ("2026-10-21", "SQ *TERP THRIFT COLLEGE PARK MD", "-18.00", "Shopping", 0.48),
    ("2026-10-27", "UMD BOOKSTORE 0042", "-42.10", None, None),
]


def add_demo_extras(db: Session, account_id: int) -> tuple[int, int]:
    """Save DEMO_RULES and add DEMO_REVIEW_ROWS to a labeled fixture account. Idempotent.

    Rows go straight into the table with fixed guesses, not through the import, so the result never depends on
    an LLM. Returns (rules saved, review rows added). Does not commit.
    """
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    from app.models import CategoryCorrection
    from app.services.merchant import normalize_merchant

    user_id = db.scalar(select(Account.user_id).where(Account.id == account_id))
    names = dict(db.execute(select(Category.name, Category.id)).all())

    csv_bytes = ("date,description,amount\n" + "".join(f"{d},{desc},{a}\n" for d, desc, a, _, _ in DEMO_REVIEW_ROWS)).encode()
    added = 0
    for (d, desc, amount, guess, confidence), h in zip(DEMO_REVIEW_ROWS, import_hashes(parse_csv(csv_bytes))):
        if db.scalar(select(Transaction.id).where(Transaction.account_id == account_id, Transaction.import_hash == h)):
            continue
        db.add(Transaction(
            account_id=account_id, date=date.fromisoformat(d), amount=Decimal(amount), raw_description=desc,
            merchant=normalize_merchant(desc), import_hash=h,
            category_id=names[guess] if guess else None,
            category_source=CategorySource.LLM if guess else None, category_confidence=confidence,
        ))
        added += 1

    for merchant, category in DEMO_RULES.items():
        db.execute(mysql_insert(CategoryCorrection)
                   .values(user_id=user_id, merchant=merchant, category_id=names[category])
                   .on_duplicate_key_update(category_id=names[category]))
        # the fixture rows the rule covers were labeled as set by hand; mark them as the rule's, same category
        for t in db.scalars(select(Transaction).where(Transaction.account_id == account_id,
                                                      Transaction.merchant == merchant,
                                                      Transaction.category_id == names[category])):
            t.category_source, t.category_confidence = CategorySource.CORRECTION, 1.0
    db.flush()
    refresh_insights(db, user_id)
    return len(DEMO_RULES), added


def delete_user(db: Session, email: str) -> bool:
    """Delete a user and, by cascade, everything they own. True if they existed. Does not commit."""
    from sqlalchemy import delete

    from app.models import User

    # A SQL DELETE, so the database's ON DELETE CASCADE removes the rest (the ORM would try to null accounts.user_id).
    deleted = db.execute(delete(User).where(User.email == email)).rowcount
    db.expire_all()
    return deleted > 0
