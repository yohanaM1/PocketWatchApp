"""Monthly budgets: spending per category in a month and each budget's status (see docs/specs/budgets.md).

Spending uses the one shared definition (app/agent/spending.py), so a budget's spent figure matches a verified
answer for the same category and month: -(sum of signed amounts), and review-queue guesses don't count yet.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.agent.spending import SPENDING_CATEGORIES, counted_category_id
from app.models import Account, Category, Transaction
from app.services.review import needs_review

ZERO = Decimal("0.00")
Status = Literal["ok", "near", "over"]


def parse_month(month: str) -> date:
    """'2026-10' -> date(2026, 10, 1). The caller has already checked the YYYY-MM format."""
    return date(int(month[:4]), int(month[5:]), 1)


def month_key(d: date) -> str:
    """date(2026, 10, 17) -> '2026-10'."""
    return f"{d.year:04d}-{d.month:02d}"


def month_end(first: date) -> date:
    """The last day of first's month."""
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def previous_month(first: date) -> date:
    """The first day of the month before."""
    return (first - timedelta(days=1)).replace(day=1)


def budget_status(spent: Decimal, limit: Decimal) -> Status:
    """over when spent > limit; near when spent >= BUDGET_NEAR_FRACTION x limit; else ok. Exactly the limit is near."""
    if spent > limit:
        return "over"
    if spent >= limit * config.BUDGET_NEAR_FRACTION:
        return "near"
    return "ok"


def budget_percent(spent: Decimal, limit: Decimal) -> int:
    """spent / limit x 100, rounded half up to a whole number. limit is always > 0 (the API enforces it)."""
    return int((spent * 100 / limit).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money(d: Decimal) -> str:
    """A 2-decimal string; ZERO + d so zero is "0.00", never "-0.00"."""
    return f"{ZERO + d:.2f}"


def spending_by_category(db: Session, user_id: int, first: date) -> dict[str, Decimal]:
    """{category name: spending} for every spending category in first's month (0.00 when nothing was spent)."""
    category = counted_category_id().label("category_id")  # NULL while awaiting review
    rows = db.execute(
        select(Category.name, func.coalesce(func.sum(Transaction.amount), 0))
        .select_from(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .join(Category, Category.id == category)
        .where(Account.user_id == user_id, Transaction.date.between(first, month_end(first)))
        .group_by(Category.name)
    ).all()
    net = {name: Decimal(total) for name, total in rows}
    return {name: ZERO - net.get(name, ZERO) for name in SPENDING_CATEGORIES}


def pending_review_count(db: Session, user_id: int, first: date) -> int:
    """Transactions in first's month that needs_review() holds back from spending."""
    return db.scalar(
        select(func.count()).select_from(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id, Transaction.date.between(first, month_end(first)), needs_review())
    ) or 0


def latest_transaction_date(db: Session, user_id: int) -> date | None:
    """The user's latest transaction date across all accounts (the page's as_of), or None before any import."""
    return db.scalar(
        select(func.max(Transaction.date)).join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id)
    )


def has_transactions(db: Session, user_id: int, first: date) -> bool:
    """Whether the user has any transaction in first's month (else last month's spending shows as unknown, not $0)."""
    return db.scalar(
        select(func.count()).select_from(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id, Transaction.date.between(first, month_end(first)))
    ) > 0
