"""Recurring payments and flagged transactions, as stored by app/services/insights.py."""

from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models import Account, AnomalyFlag, AnomalyStatus, Category, RecurringSeries, Transaction
from app.routers.transactions import owned_transactions, transaction_out
from app.schemas import FlagOut, FlagPage, FlagUpdate, RecurringPage, RecurringSeriesOut, TransactionOut

router = APIRouter(tags=["insights"])

ZERO = Decimal("0.00")


def _money(d: Decimal | None) -> str | None:
    """A 2-decimal string, like every amount the API returns."""
    return None if d is None else f"{d:.2f}"


# GET /recurring
@router.get("/recurring", response_model=RecurringPage)
def list_recurring(user: CurrentUser, db: DbSession) -> RecurringPage:
    """Your recurring payments: active ones first (largest first), then stopped ones.

    Detected after every import and category change. "Active" and "next expected" are relative to your latest
    transaction (`as_of`), not today, so older statements don't make everything look cancelled.
    """
    stored = db.scalars(
        select(RecurringSeries).where(RecurringSeries.user_id == user.id)
        .order_by(RecurringSeries.active.desc(), RecurringSeries.typical_amount, RecurringSeries.merchant)
    ).all()
    as_of = db.scalar(select(func.max(Transaction.date)).join(Account, Account.id == Transaction.account_id)
                      .where(Account.user_id == user.id))

    # The category of each series' latest charge.
    latest = {}
    if stored:
        rows = db.execute(
            select(Transaction.recurring_series_id, Category.name)
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(Transaction.recurring_series_id.in_([s.id for s in stored]))
            .order_by(Transaction.date, Transaction.id)
        ).all()
        latest = {series_id: category for series_id, category in rows}  # later rows overwrite earlier ones

    active = [s for s in stored if s.active]
    return RecurringPage(
        as_of=as_of,
        monthly_total=_money(sum((s.typical_amount for s in active), ZERO)),
        active_count=len(active),
        series=[RecurringSeriesOut(
            key=s.key, merchant=s.merchant, category=latest.get(s.id), amount=_money(s.typical_amount),
            previous_amount=_money(s.previous_amount), amount_changed_on=s.amount_changed_on,
            interval_days=s.interval_days, first_date=s.first_date, last_date=s.last_date,
            next_expected=s.last_date + timedelta(days=s.interval_days) if s.active else None,
            charge_count=s.charge_count, active=s.active,
        ) for s in stored],
    )


# GET /recurring/transactions?key=
@router.get("/recurring/transactions", response_model=list[TransactionOut])
def recurring_transactions(key: Annotated[str, Query(min_length=1, max_length=150)], user: CurrentUser,
                           db: DbSession) -> list[TransactionOut]:
    """The charges in one of your recurring series, newest first.

    A query parameter, because keys contain spaces and colons. 404 for an unknown key or another user's key
    (keys can change when you import older statements; refetch GET /recurring then).
    """
    series_id = db.scalar(select(RecurringSeries.id).where(RecurringSeries.user_id == user.id,
                                                           RecurringSeries.key == key))
    if series_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recurring payment not found")
    rows = db.execute(owned_transactions(user).where(Transaction.recurring_series_id == series_id)
                      .order_by(Transaction.date.desc(), Transaction.id.desc())).all()
    return [transaction_out(*r) for r in rows]


def _owned_flags(user):
    """Every flag query starts here, so users only ever reach flags on their own transactions."""
    return (select(AnomalyFlag).join(Transaction, Transaction.id == AnomalyFlag.transaction_id)
            .join(Account, Account.id == Transaction.account_id).where(Account.user_id == user.id))


def _open_count(db, user) -> int:
    """How many of the user's flags are open."""
    return db.scalar(select(func.count()).select_from(
        _owned_flags(user).where(AnomalyFlag.status == AnomalyStatus.OPEN).subquery()))


# GET /anomalies
@router.get("/anomalies", response_model=FlagPage)
def list_flags(user: CurrentUser, db: DbSession,
               status_filter: Annotated[Literal["open", "dismissed", "confirmed", "all"],
                                        Query(alias="status")] = "open") -> FlagPage:
    """Your flagged transactions (possible duplicates, unusually large charges, new merchants), newest first.

    `status` filters by open (default), dismissed, confirmed or all. `open_count` is always the open total.
    """
    query = _owned_flags(user)
    if status_filter != "all":
        query = query.where(AnomalyFlag.status == AnomalyStatus(status_filter))
    found = db.scalars(query.order_by(Transaction.date.desc(), AnomalyFlag.id.desc())).all()
    txns = {t.id: (t, a, c) for t, a, c in db.execute(
        owned_transactions(user).where(Transaction.id.in_({f.transaction_id for f in found}))).all()} if found else {}
    return FlagPage(
        items=[_flag_out(f, txns[f.transaction_id]) for f in found],
        open_count=_open_count(db, user),
    )


def _flag_out(f: AnomalyFlag, txn) -> FlagOut:
    """One flag with its transaction."""
    return FlagOut(id=f.id, kind=f.kind, reason=f.reason, status=f.status, created_at=f.created_at,
                   transaction=transaction_out(*txn))


# PATCH /anomalies/{flag_id}
@router.patch("/anomalies/{flag_id}", response_model=FlagOut)
def update_flag(flag_id: int, body: FlagUpdate, user: CurrentUser, db: DbSession) -> FlagOut:
    """Dismiss a flag ("looks fine"), confirm it ("not right"), or reopen it.

    Confirming only records your answer; it changes no totals or other rows. 404 for a flag that isn't yours.
    """
    flag = db.scalar(_owned_flags(user).where(AnomalyFlag.id == flag_id))
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Flag not found")
    flag.status = body.status
    db.commit()
    txn = db.execute(owned_transactions(user).where(Transaction.id == flag.transaction_id)).one()
    return _flag_out(flag, txn)
