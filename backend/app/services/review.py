"""The categorization review queue: which transactions need a person to pick a category, and merchant-wide corrections.

A transaction needs review when nothing categorized it, or when the LLM pipeline guessed with low confidence.
A category set by hand (source = user) or by a saved correction (source = correction) is trusted.
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app import config
from app.models import Account, CategoryCorrection, CategorySource, Transaction, User

# Merchants that sell many kinds of things, so one category for all their transactions is usually wrong.
# Names as normalize_merchant produces them. "PAYPAL *SELLER" rows are named after the seller, so PAYPAL
# only matches bare PayPal rows.
MIXED_MERCHANTS = frozenset({
    "AMAZON", "WALMART", "TARGET", "COSTCO", "CVS", "WALGREENS", "VENMO", "ZELLE", "PAYPAL", "APPLE",
})

# category_confidence is a MySQL FLOAT (single precision): a stored 0.7 reads back as 0.699999988.
# Without this slack, a guess exactly at the threshold would count as below it.
FLOAT_SLACK = 1e-6


# Python twin of needs_review(), for one loaded row (test_unconfirmed_guesses.py checks the two agree)
def is_in_review(t: Transaction) -> bool:
    """Uncategorized, or an LLM guess below REVIEW_CONFIDENCE_THRESHOLD (a guess with no confidence counts as low)."""
    if t.category_id is None:
        return True
    return t.category_source == CategorySource.LLM and (
        t.category_confidence is None or t.category_confidence < config.REVIEW_CONFIDENCE_THRESHOLD - FLOAT_SLACK)


# SQL condition: the transaction is in the review queue
def needs_review() -> ColumnElement[bool]:
    """Uncategorized, or an LLM guess below REVIEW_CONFIDENCE_THRESHOLD (a guess with no confidence counts as low).

    The threshold is read on every call, so tests and a changed setting take effect without a restart of this module.
    """
    return or_(
        Transaction.category_id.is_(None),
        and_(
            Transaction.category_source == CategorySource.LLM,
            or_(Transaction.category_confidence.is_(None),
                Transaction.category_confidence < config.REVIEW_CONFIDENCE_THRESHOLD - FLOAT_SLACK),
        ),
    )


# Why a review group should be handled one transaction at a time
def mixed_reason(merchant: str | None, guessed_categories: int, hand_set_categories: int) -> str | None:
    """known_mixed, guesses_differ or history_differs (checked in that order), or None for a single-purpose merchant.

    guessed_categories: how many different LLM guesses the queued rows have.
    hand_set_categories: how many different categories the user has set by hand for this merchant.
    """
    if merchant in MIXED_MERCHANTS:
        return "known_mixed"
    if guessed_categories >= 2:
        return "guesses_differ"
    if hand_set_categories >= 2:
        return "history_differs"
    return None


# Other same-merchant transactions a merchant-wide change would touch
def same_merchant_ids(db: Session, user: User, merchant: str | None, category_id: int,
                      exclude_ids: Iterable[int] = ()) -> list[int]:
    """Ids of the user's transactions from `merchant` that weren't set by hand and aren't already in the category."""
    if merchant is None:
        return []
    stmt = (
        select(Transaction.id)
        .join(Account, Account.id == Transaction.account_id)
        .where(
            Account.user_id == user.id,
            Transaction.merchant == merchant,
            or_(Transaction.category_source.is_(None), Transaction.category_source != CategorySource.USER),
            or_(Transaction.category_id.is_(None), Transaction.category_id != category_id),
        )
    )
    exclude = list(exclude_ids)
    if exclude:
        stmt = stmt.where(Transaction.id.not_in(exclude))
    return list(db.scalars(stmt))


# Save a merchant correction and apply it to the merchant's other rows
def apply_merchant_correction(db: Session, user: User, merchant: str | None, category_id: int,
                              exclude_ids: Iterable[int] = ()) -> int:
    """Upsert the user's correction for `merchant` (reused by future imports) and recategorize their other rows
    from that merchant that weren't set by hand (source = correction). Returns how many rows changed.
    Does not commit.
    """
    if merchant is None:
        return 0
    db.execute(
        mysql_insert(CategoryCorrection)
        .values(user_id=user.id, merchant=merchant, category_id=category_id)
        .on_duplicate_key_update(category_id=category_id)
    )
    ids = same_merchant_ids(db, user, merchant, category_id, exclude_ids)
    if ids:
        db.execute(
            update(Transaction).where(Transaction.id.in_(ids))
            .values(category_id=category_id, category_source=CategorySource.CORRECTION, category_confidence=1.0)
            .execution_options(synchronize_session=False)
        )
    return len(ids)
