"""Monthly budgets per spending category (docs/specs/budgets.md). Every query is scoped to the current user."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.spending import SPENDING_CATEGORIES
from app.deps import CurrentUser, DbSession
from app.models import Budget, Category, User
from app.schemas import (
    BudgetCopy,
    BudgetCopyResult,
    BudgetPage,
    BudgetRowOut,
    BudgetSet,
    MonthStr,
)
from app.services.budgets import (
    ZERO,
    budget_percent,
    budget_status,
    has_transactions,
    latest_transaction_date,
    money,
    month_key,
    parse_month,
    pending_review_count,
    previous_month,
    spending_by_category,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _row(category: str, budget: Budget | None, spent: Decimal, previous: Decimal | None) -> BudgetRowOut:
    """One spending category's row: its budget (if any) and spending, as the API returns it."""
    prev = None if previous is None else money(previous)
    if budget is None:
        return BudgetRowOut(category=category, budget_id=None, limit=None, spent=money(spent), left=None,
                            percent=None, status="none", previous_spent=prev)
    return BudgetRowOut(
        category=category, budget_id=budget.id, limit=money(budget.limit_amount), spent=money(spent),
        left=money(budget.limit_amount - spent), percent=budget_percent(spent, budget.limit_amount),
        status=budget_status(spent, budget.limit_amount), previous_spent=prev,
    )


def _previous_spending(db: Session, user: User, first: date) -> dict[str, Decimal] | None:
    """Spending per category in the month before, or None when nothing was imported for it."""
    prev = previous_month(first)
    return spending_by_category(db, user.id, prev) if has_transactions(db, user.id, prev) else None


def _month_budgets(db: Session, user: User, first: date) -> list[tuple[Budget, str]]:
    """The user's budgets in first's month, with their category names."""
    return list(db.execute(
        select(Budget, Category.name).join(Category, Category.id == Budget.category_id)
        .where(Budget.user_id == user.id, Budget.month == first)
    ).all())


def _spending_category_id(db: Session, category: str) -> int:
    """The id of a spending category; 422 for Income, Transfers or an unknown name."""
    if category not in SPENDING_CATEGORIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"Budgets are for spending categories only, not {category!r}")
    return db.scalar(select(Category.id).where(Category.name == category))


# GET /budgets
@router.get("", response_model=BudgetPage)
def get_budgets(user: CurrentUser, db: DbSession, month: Annotated[MonthStr | None, Query()] = None) -> BudgetPage:
    """Your budgets for a month (YYYY-MM) with what you've spent against each.

    Without `month`, the month of your latest transaction (today's month before any import), so older statements
    don't open on an empty month. Spending is the same definition verified answers use: transactions waiting in the
    review queue aren't counted yet, and `pending_review_count` says how many there are.
    """
    as_of = latest_transaction_date(db, user.id)
    today_first = date.today().replace(day=1)
    data_first = as_of.replace(day=1) if as_of else today_first
    first = parse_month(month) if month else data_first

    spending = spending_by_category(db, user.id, first)
    previous = _previous_spending(db, user, first)
    by_name = {name: b for b, name in _month_budgets(db, user, first)}
    rows = [_row(name, by_name.get(name), spending[name], previous and previous[name])
            for name in SPENDING_CATEGORIES]
    total_limit = sum((b.limit_amount for b in by_name.values()), ZERO)
    total_spent = sum((spending[name] for name in by_name), ZERO)
    previous_count = db.scalar(
        select(func.count()).select_from(Budget)
        .where(Budget.user_id == user.id, Budget.month == previous_month(first))
    ) or 0

    return BudgetPage(
        month=month_key(first), as_of=as_of, max_month=month_key(max(data_first, today_first)),
        budgeted=money(total_limit), spent=money(total_spent), left=money(total_limit - total_spent),
        over_count=sum(1 for r in rows if r.status == "over"), budget_count=len(by_name), rows=rows,
        pending_review_count=pending_review_count(db, user.id, first), previous_month_count=previous_count,
    )


# PUT /budgets/{month}/{category}
@router.put("/{month}/{category}", response_model=BudgetRowOut)
def set_budget(
    user: CurrentUser, db: DbSession, month: Annotated[MonthStr, Path()], category: str, body: BudgetSet,
) -> BudgetRowOut:
    """Create or replace your budget for one spending category in one month. Returns its row for that month."""
    category_id = _spending_category_id(db, category)
    first = parse_month(month)
    budget = db.scalar(select(Budget).where(Budget.user_id == user.id, Budget.category_id == category_id,
                                            Budget.month == first))
    if budget is None:
        budget = Budget(user_id=user.id, category_id=category_id, month=first, limit_amount=body.limit)
        db.add(budget)
    else:
        budget.limit_amount = body.limit
    db.commit()
    db.refresh(budget)
    previous = _previous_spending(db, user, first)
    return _row(category, budget, spending_by_category(db, user.id, first)[category], previous and previous[category])


# DELETE /budgets/{id}
@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_budget(user: CurrentUser, db: DbSession, budget_id: int) -> Response:
    """Remove one of your budgets. Someone else's (or a missing id) is 404, so its existence isn't revealed."""
    budget = db.scalar(select(Budget).where(Budget.id == budget_id, Budget.user_id == user.id))
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Budget not found")
    db.delete(budget)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# POST /budgets/copy
@router.post("/copy", response_model=BudgetCopyResult)
def copy_budgets(user: CurrentUser, db: DbSession, body: BudgetCopy) -> BudgetCopyResult:
    """Copy every budget in from_month to to_month, skipping categories to_month already has."""
    if body.from_month == body.to_month:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="from_month and to_month are the same")
    source, target = parse_month(body.from_month), parse_month(body.to_month)
    existing = set(db.scalars(select(Budget.category_id).where(Budget.user_id == user.id, Budget.month == target)))
    copied = skipped = 0
    for b in db.scalars(select(Budget).where(Budget.user_id == user.id, Budget.month == source)):
        if b.category_id in existing:
            skipped += 1
            continue
        db.add(Budget(user_id=user.id, category_id=b.category_id, month=target, limit_amount=b.limit_amount))
        copied += 1
    db.commit()
    return BudgetCopyResult(copied=copied, skipped=skipped)
