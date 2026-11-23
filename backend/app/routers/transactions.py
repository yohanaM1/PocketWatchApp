from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, case, func, select

from app.agent.spending import counted_category_id
from app.categories import DEFAULT_CATEGORIES
from app.deps import CurrentUser, DbSession, get_owned_account
from app.models import Account, Category, CategorySource, Transaction, User
from app.schemas import CategoryUpdate, CategoryUpdateResult, TransactionOut, TransactionPage
from app.services.insights import safe_refresh_insights
from app.services.review import apply_merchant_correction, is_in_review, needs_review, same_merchant_ids

router = APIRouter(tags=["transactions"])

UNCATEGORIZED = "uncategorized"
ZERO = Decimal("0.00")


# Base query: one user's transactions with their account and category names
def owned_transactions(user: User) -> Select:
    """Every transaction query starts here so users only ever reach their own rows."""
    return (
        select(Transaction, Account.name, Category.name)
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(Account.user_id == user.id)
    )


# Row -> response model
def transaction_out(t: Transaction, account_name: str, category: str | None) -> TransactionOut:
    """One transaction as the API returns it; the amount is a 2-decimal string."""
    return TransactionOut(
        id=t.id, account_id=t.account_id, account_name=account_name, date=t.date, amount=f"{t.amount:.2f}",
        merchant=t.merchant, description=t.raw_description, category=category, category_source=t.category_source,
        category_confidence=t.category_confidence, in_review=is_in_review(t),
    )


# GET /categories
@router.get("/categories", response_model=list[str])
def list_categories(user: CurrentUser) -> list[str]:
    """The fixed category list, in display order."""
    return DEFAULT_CATEGORIES


# GET /transactions
@router.get("/transactions", response_model=TransactionPage)
def list_transactions(
    user: CurrentUser,
    db: DbSession,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")] = None,
    account_id: int | None = None,
    category: str | None = None,
    merchant: Annotated[str | None, Query(max_length=100)] = None,
    review: bool = False,
    counted: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    """Your transactions, newest first, optionally filtered by month (YYYY-MM), account, category and merchant.

    `category` is a category name or `uncategorized`. `review=true` keeps only rows in the review queue
    (with `limit=1`, `total` is the queue size). `counted=true` drops rows in the review queue instead, keeping only
    those counted in spending (what the Budgets page lists). `total` is the count across all pages.
    """
    stmt = owned_transactions(user)
    if month is not None:
        start = date(int(month[:4]), int(month[5:]), 1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        stmt = stmt.where(Transaction.date.between(start, end))
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == get_owned_account(db, account_id, user).id)
    if category is not None:
        if category == UNCATEGORIZED:
            stmt = stmt.where(Transaction.category_id.is_(None))
        elif category in DEFAULT_CATEGORIES:
            stmt = stmt.where(Category.name == category)
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown category: {category}")
    if merchant is not None:
        stmt = stmt.where(Transaction.merchant == merchant)
    if review:
        stmt = stmt.where(needs_review())
    if counted:  # the same rule spending uses, so a NULL in needs_review() can't make the two disagree
        stmt = stmt.where(counted_category_id().is_not(None))

    sub = stmt.subquery()
    total, out_sum, in_sum, uncategorized = db.execute(select(
        func.count(),
        func.coalesce(func.sum(case((sub.c.amount < 0, sub.c.amount), else_=0)), 0),
        func.coalesce(func.sum(case((sub.c.amount > 0, sub.c.amount), else_=0)), 0),
        func.coalesce(func.sum(case((sub.c.category_id.is_(None), 1), else_=0)), 0),
    )).one()
    review_count = db.scalar(select(func.count()).select_from(stmt.where(needs_review()).subquery()))
    rows = db.execute(stmt.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(limit).offset(offset)).all()
    return TransactionPage(items=[transaction_out(*r) for r in rows], total=total,
                           money_out=f"{ZERO - out_sum:.2f}", money_in=f"{ZERO + in_sum:.2f}",
                           uncategorized_count=int(uncategorized), review_count=review_count)


# PATCH /transactions/{transaction_id}
@router.patch("/transactions/{transaction_id}", response_model=CategoryUpdateResult)
def update_category(transaction_id: int, body: CategoryUpdate, user: CurrentUser, db: DbSession) -> CategoryUpdateResult:
    """Set one transaction's category by hand.

    With `apply_to_merchant`, also save the choice as a correction for the merchant (reused by future imports)
    and apply it to your other transactions from that merchant, except ones you set by hand.
    404 for a transaction that isn't yours.
    """
    row = db.execute(owned_transactions(user).where(Transaction.id == transaction_id)).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Transaction not found")
    t, account_name, _ = row
    category_id = db.scalar(select(Category.id).where(Category.name == body.category))

    t.category_id, t.category_source, t.category_confidence = category_id, CategorySource.USER, 1.0
    also_updated = same_merchant_count = 0
    if body.apply_to_merchant and t.merchant is not None:
        also_updated = apply_merchant_correction(db, user, t.merchant, category_id, exclude_ids=[t.id])
    else:
        same_merchant_count = len(same_merchant_ids(db, user, t.merchant, category_id, exclude_ids=[t.id]))
    safe_refresh_insights(db, user.id)  # a category change can add a large-charge flag
    db.commit()

    return CategoryUpdateResult(transaction=transaction_out(t, account_name, body.category), also_updated=also_updated,
                                same_merchant_count=same_merchant_count)
