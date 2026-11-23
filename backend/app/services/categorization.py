from collections import defaultdict

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app import config
from app.agent.llm import LLM
from app.models import Account, CategoryCorrection, CategorySource, Category, Transaction
from app.services.llm_categorizer import build_groups, categorize_groups, split_suspicious


# Categorization hook run after each import
def categorize_new_transactions(db: Session, transaction_ids: list[int], llm: LLM | None = None) -> None:
    """Called after every CSV import with the ids of the newly inserted transactions.

    Step 1: apply the user's saved category_corrections by merchant (source = correction, confidence = 1.0).
    Step 2 (only when `llm` is given): commit, then ask the LLM about what is still uncategorized.
    Only rows with category_id NULL are touched, and never one whose source is user.
    """
    if not transaction_ids:
        return
    apply_corrections(db, transaction_ids)
    if llm is not None:
        db.commit()  # the imported rows are saved before the slow LLM step, which then can't lose them
        categorize_with_llm(db, transaction_ids, llm)


def apply_corrections(db: Session, transaction_ids: list[int]) -> None:
    """Set the category of each transaction whose merchant the user has a saved correction for."""
    matches = db.execute(
        select(Transaction.id, CategoryCorrection.category_id)
        .join(Account, Account.id == Transaction.account_id)
        .join(CategoryCorrection, and_(CategoryCorrection.user_id == Account.user_id,
                                       CategoryCorrection.merchant == Transaction.merchant))
        .where(Transaction.id.in_(transaction_ids))
    ).all()
    by_category: dict[int, list[int]] = defaultdict(list)
    for txn_id, category_id in matches:
        by_category[category_id].append(txn_id)
    for category_id, ids in by_category.items():
        db.execute(
            update(Transaction).where(Transaction.id.in_(ids))
            .values(category_id=category_id, category_source=CategorySource.CORRECTION, category_confidence=1.0)
            .execution_options(synchronize_session=False)
        )


def categorize_with_llm(db: Session, transaction_ids: list[int], llm: LLM) -> dict[str, int]:
    """LLM guesses for the still-uncategorized rows among `transaction_ids`; returns counts by outcome.

    Every answered guess is saved: category_id = the guess, source = llm, category_confidence = the
    LLM's confidence. A guess below REVIEW_CONFIDENCE_THRESHOLD waits in the review queue
    (app/services/review.py), where the user sees it as a suggestion, and verified answers report it
    as not categorized yet (app/agent/spending.py). A guess code overruled (llm_categorizer.checked,
    e.g. money out claimed to be a transfer) is saved with confidence NULL, which the queue treats as
    low whatever the LLM said. Groups the LLM didn't answer stay untouched (uncategorized).
    """
    rows = db.execute(
        select(Transaction.id, Transaction.merchant, Transaction.raw_description, Transaction.amount)
        .where(Transaction.id.in_(transaction_ids), Transaction.category_id.is_(None),
               or_(Transaction.category_source.is_(None), Transaction.category_source != CategorySource.USER))
        .order_by(Transaction.id)
    ).all()
    counts = {"categorized": 0, "needs_review": 0, "not_answered": 0, "held_back": 0}
    if not rows:
        return counts
    safe, held = split_suspicious([tuple(r) for r in rows])
    counts["held_back"] = len(held)  # never sent: their text reads like instructions to the model
    if not safe:
        return counts
    groups = build_groups(safe)
    db.commit()  # don't hold a transaction open while waiting on the LLM
    guesses = categorize_groups(llm, groups)

    category_ids = dict(db.execute(select(Category.name, Category.id)).all())
    for g in groups:
        guess = guesses.get(g.key)
        if guess is None:
            counts["not_answered"] += len(g.transaction_ids)
            continue
        accepted = guess.applies()
        db.execute(
            update(Transaction)
            # re-checked here: a user may have set a category by hand while the LLM was working
            .where(Transaction.id.in_(g.transaction_ids), Transaction.category_id.is_(None),
                   or_(Transaction.category_source.is_(None), Transaction.category_source != CategorySource.USER))
            .values(category_id=category_ids[guess.category], category_source=CategorySource.LLM,
                    category_confidence=None if guess.review else round(guess.confidence, 3))
            .execution_options(synchronize_session=False)
        )
        counts["categorized" if accepted else "needs_review"] += len(g.transaction_ids)
    return counts
