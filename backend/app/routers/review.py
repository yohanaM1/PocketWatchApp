from collections import Counter, defaultdict
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.deps import CurrentUser, DbSession
from app.models import Account, Category, CategorySource, Transaction, User
from app.routers.transactions import owned_transactions, transaction_out
from app.schemas import ReviewGroup, ReviewGuess, ReviewPage, ReviewResolve, ReviewResolveResult
from app.services.insights import safe_refresh_insights
from app.services.review import apply_merchant_correction, mixed_reason, needs_review

router = APIRouter(tags=["review"])

ZERO = Decimal("0.00")
EXAMPLES_PER_GROUP = 3

# A queued row as owned_transactions() returns it: (transaction, account name, category name)
Row = tuple[Transaction, str, str | None]


# How many different categories the user has set by hand, per merchant
def _hand_set_category_counts(db: Session, user: User, merchants: list[str]) -> dict[str, int]:
    """{merchant: number of distinct categories on the user's source = user rows}, for the history_differs signal."""
    if not merchants:
        return {}
    return dict(db.execute(
        select(Transaction.merchant, func.count(func.distinct(Transaction.category_id)))
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user.id, Transaction.merchant.in_(merchants),
               Transaction.category_source == CategorySource.USER)
        .group_by(Transaction.merchant)
    ).all())


# One merchant's queued rows -> a review card
def _group(merchant: str | None, rows: list[Row], hand_set_categories: int) -> ReviewGroup:
    """Summarize a merchant's queued rows (given newest first): guesses, mixed signal, money and date range."""
    counts: Counter[str] = Counter()
    lowest: dict[str, float] = {}
    for t, _, category in rows:
        if t.category_source == CategorySource.LLM and category is not None:
            counts[category] += 1
            if t.category_confidence is not None:
                lowest[category] = min(lowest.get(category, t.category_confidence), t.category_confidence)
    # Most common first; ties go to the more confident guess (no confidence sorts last), then by name.
    order = sorted(counts, key=lambda c: (-counts[c], -lowest.get(c, -1.0), c))
    guesses = [ReviewGuess(category=c, count=counts[c], confidence=lowest.get(c)) for c in order]
    reason = mixed_reason(merchant, len(guesses), hand_set_categories)
    money_out = sum((t.amount for t, _, _ in rows if t.amount < 0), ZERO)
    money_in = sum((t.amount for t, _, _ in rows if t.amount > 0), ZERO)
    dates = [t.date for t, _, _ in rows]
    return ReviewGroup(
        merchant=merchant,
        count=len(rows),
        reason="uncategorized" if any(category is None for _, _, category in rows) else "low_confidence",
        guesses=guesses,
        suggestion=guesses[0].category if guesses else None,
        mixed=reason is not None,
        mixed_reason=reason,
        money_out=f"{ZERO - money_out:.2f}",
        money_in=f"{ZERO + money_in:.2f}",
        first_date=min(dates),
        last_date=max(dates),
        examples=[transaction_out(*r) for r in rows[:EXAMPLES_PER_GROUP]],
    )


# GET /review
@router.get("/review", response_model=ReviewPage)
def list_review(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewPage:
    """Your transactions that need a category, grouped by merchant, biggest groups first.

    A row is queued when it's uncategorized or an LLM guess below REVIEW_CONFIDENCE_THRESHOLD.
    `limit`/`offset` page over groups.
    """
    rows = db.execute(
        owned_transactions(user).where(needs_review()).order_by(Transaction.date.desc(), Transaction.id.desc())
    ).all()
    by_merchant: dict[str | None, list[Row]] = defaultdict(list)
    for t, account_name, category in rows:
        by_merchant[t.merchant].append((t, account_name, category))
    ordered = sorted(by_merchant.items(), key=lambda item: (-len(item[1]), item[0] or ""))
    page = ordered[offset:offset + limit]
    hand_set = _hand_set_category_counts(db, user, [m for m, _ in page if m is not None])
    return ReviewPage(
        groups=[_group(m, members, hand_set.get(m, 0) if m is not None else 0) for m, members in page],
        total_groups=len(ordered),
        total_transactions=len(rows),
    )


# POST /review/resolve
@router.post("/review/resolve", response_model=ReviewResolveResult)
def resolve_group(body: ReviewResolve, user: CurrentUser, db: DbSession) -> ReviewResolveResult:
    """Set every queued transaction from one merchant to a category (as if set by hand).

    With `remember` (the default), also save the correction for the merchant, so future imports are
    categorized on arrival, and apply it to the merchant's other rows not set by hand.
    404 if nothing from that merchant is queued for you.
    """
    merchant_match = Transaction.merchant.is_(None) if body.merchant is None else Transaction.merchant == body.merchant
    ids = list(db.scalars(
        select(Transaction.id)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user.id, merchant_match, needs_review())
    ))
    if not ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Nothing from this merchant needs review")
    category_id = db.scalar(select(Category.id).where(Category.name == body.category))

    db.execute(
        update(Transaction).where(Transaction.id.in_(ids))
        .values(category_id=category_id, category_source=CategorySource.USER, category_confidence=1.0)
        .execution_options(synchronize_session=False)
    )
    also_updated = apply_merchant_correction(db, user, body.merchant, category_id, exclude_ids=ids) if body.remember else 0
    safe_refresh_insights(db, user.id)  # a category change can add a large-charge flag
    db.commit()
    return ReviewResolveResult(updated=len(ids), also_updated=also_updated)
