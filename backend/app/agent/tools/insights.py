"""find_recurring and detect_anomalies: the stored results behind list_plan answers (app/services/insights.py
keeps them current after every import and category change). Both describe the present, so they take no period.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.plan import ListPlan
from app.models import Account, AnomalyFlag, AnomalyStatus, RecurringSeries, Transaction

ZERO = Decimal("0.00")


def _coverage(db: Session, user_id: int) -> dict:
    """The user's latest transaction date and how many transactions they have."""
    count, last = db.execute(
        select(func.count(Transaction.id), func.max(Transaction.date))
        .join(Account, Account.id == Transaction.account_id).where(Account.user_id == user_id)
    ).one()
    return {"row_id": "coverage", "last_date": last.isoformat() if last else None, "count": count}


def find_recurring(db: Session, user_id: int, args: ListPlan, today: date) -> dict:
    """Active series (largest first, then merchant A-Z), their total, how many have stopped, and coverage."""
    stored = db.scalars(select(RecurringSeries).where(RecurringSeries.user_id == user_id)
                        .order_by(RecurringSeries.typical_amount, RecurringSeries.merchant)).all()
    active = [s for s in stored if s.active]
    rows = [{"row_id": f"series:{s.key}", "key": s.key, "merchant": s.merchant, "amount": f"{s.typical_amount:.2f}",
             "next_expected": (s.last_date + timedelta(days=s.interval_days)).isoformat()} for s in active]
    total = sum((s.typical_amount for s in active), ZERO)
    return {
        "rows": rows,
        "total": {"row_id": "rectotal", "amount": f"{total:.2f}", "count": len(rows)},
        "stopped": {"row_id": "stopped", "count": len(stored) - len(active)},
        "coverage": _coverage(db, user_id),
    }


def detect_anomalies(db: Session, user_id: int, args: ListPlan, today: date) -> dict:
    """Open flags (newest transaction first), how many, and coverage."""
    found = db.execute(
        select(AnomalyFlag.id, AnomalyFlag.kind, Transaction.merchant, Transaction.amount, Transaction.date)
        .join(Transaction, Transaction.id == AnomalyFlag.transaction_id)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id, AnomalyFlag.status == AnomalyStatus.OPEN)
        .order_by(Transaction.date.desc(), AnomalyFlag.id.desc())
    ).all()
    rows = [{"row_id": f"flag:{fid}", "kind": kind.value, "merchant": merchant, "amount": f"{amount:.2f}",
             "date": d.isoformat()} for fid, kind, merchant, amount, d in found]
    return {
        "rows": rows,
        "total": {"row_id": "flagcount", "count": len(rows)},
        "coverage": _coverage(db, user_id),
    }
