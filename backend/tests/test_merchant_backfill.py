from sqlalchemy import select, text

from app.models import Account, AccountType, Category, CategoryCorrection, Transaction, User
from app.services.merchant_backfill import renormalize_merchants


# Helper: a user with one account
def make_user(db, email):
    """A user plus a checking account; returns (user, account)."""
    user = User(email=email, password_hash="x")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, name="Checking", type=AccountType.CHECKING)
    db.add(account)
    db.flush()
    return user, account


# Helper: a stored transaction with an old-rules merchant name
def add_txn(db, account, raw, merchant, n=0):
    """Insert a transaction as an import under the old rules would have stored it."""
    t = Transaction(account_id=account.id, date="2026-09-01", amount=-5, raw_description=raw,
                    merchant=merchant, import_hash=f"{raw}-{n}")
    db.add(t)
    db.flush()
    return t


# Helper: a category id by name
def cat(db, name):
    """The id of a seeded category."""
    return db.scalar(select(Category.id).where(Category.name == name))


# Helper: a user's corrections as {merchant: category id}
def corrections(db, user):
    """Current corrections for the user."""
    return dict(db.execute(select(CategoryCorrection.merchant, CategoryCorrection.category_id)
                           .where(CategoryCorrection.user_id == user.id)).all())


# Transactions and corrections move to the new names
def test_renames_transactions_and_learns_correction_names_from_them(db):
    """"TRADER JOE" (old rules) becomes "TRADER JOES"; its correction follows, which re-normalizing the name alone couldn't do."""
    user, account = make_user(db, "a@umd.edu")
    t = add_txn(db, account, "TRADER JOE S #097 HYATTSVILLE MD", "TRADER JOE")
    keep = add_txn(db, account, "STARBUCKS 123", "STARBUCKS")
    db.add(CategoryCorrection(user_id=user.id, merchant="TRADER JOE", category_id=cat(db, "Groceries")))
    db.flush()

    stats = renormalize_merchants(db.connection())
    db.expire_all()
    assert db.get(Transaction, t.id).merchant == "TRADER JOES"
    assert db.get(Transaction, keep.id).merchant == "STARBUCKS"
    assert corrections(db, user) == {"TRADER JOES": cat(db, "Groceries")}
    assert stats == {"transactions": 1, "corrections_renamed": 1, "corrections_merged": 0}


# Two corrections merging into one name
def test_merged_corrections_keep_the_newest(db):
    """TRADER JOE and TRADER JOES are one merchant now; the later correction wins and the unique key holds."""
    user, account = make_user(db, "a@umd.edu")
    add_txn(db, account, "TRADER JOE S #097", "TRADER JOE")
    add_txn(db, account, "TRADER JOES #057", "TRADER JOES")
    db.add(CategoryCorrection(user_id=user.id, merchant="TRADER JOE", category_id=cat(db, "Dining")))
    db.flush()
    db.add(CategoryCorrection(user_id=user.id, merchant="TRADER JOES", category_id=cat(db, "Groceries")))
    db.flush()

    stats = renormalize_merchants(db.connection())
    db.expire_all()
    assert corrections(db, user) == {"TRADER JOES": cat(db, "Groceries")}
    assert stats["corrections_merged"] == 1


# A correction with no transactions behind it
def test_correction_without_transactions_falls_back_to_its_own_name(db):
    """With nothing to learn from, the stored name itself is re-normalized."""
    user, _ = make_user(db, "a@umd.edu")
    db.add(CategoryCorrection(user_id=user.id, merchant="UNIV OF MD DINING", category_id=cat(db, "Dining")))
    db.flush()
    renormalize_merchants(db.connection())
    db.expire_all()
    assert corrections(db, user) == {"UMD DINING": cat(db, "Dining")}


# Users don't affect each other
def test_mappings_are_per_user(db):
    """One user's transactions never rename or merge another user's corrections."""
    a, a_account = make_user(db, "a@umd.edu")
    b, _ = make_user(db, "b@umd.edu")
    add_txn(db, a_account, "TRADER JOE S #097", "TRADER JOE")
    db.add(CategoryCorrection(user_id=a.id, merchant="TRADER JOE", category_id=cat(db, "Groceries")))
    db.add(CategoryCorrection(user_id=b.id, merchant="TRADER JOES", category_id=cat(db, "Dining")))
    db.flush()
    renormalize_merchants(db.connection())
    db.expire_all()
    assert corrections(db, a) == {"TRADER JOES": cat(db, "Groceries")}
    assert corrections(db, b) == {"TRADER JOES": cat(db, "Dining")}


# Running it again
def test_second_run_changes_nothing(db):
    """Idempotent: after one run every stored name already matches the rules."""
    user, account = make_user(db, "a@umd.edu")
    add_txn(db, account, "TRADER JOE S #097", "TRADER JOE")
    db.add(CategoryCorrection(user_id=user.id, merchant="TRADER JOE", category_id=cat(db, "Groceries")))
    db.flush()
    renormalize_merchants(db.connection())
    assert renormalize_merchants(db.connection()) == {"transactions": 0, "corrections_renamed": 0, "corrections_merged": 0}
    assert db.execute(text("SELECT COUNT(*) FROM category_corrections WHERE merchant LIKE '\\_\\_renormalize%'")).scalar() == 0
