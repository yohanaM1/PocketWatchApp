"""get_transactions: one user's transactions in a period, capped at AGENT_ROW_CAP, plus the true counts."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.agent.periods import period_json, resolve
from app.agent.plan import GetTransactionsArgs
from app.agent.spending import counted_category_id
from app.agent.tools.spending import coverage_row
from app.models import Account, Category, Transaction


def get_transactions(db: Session, user_id: int, args: GetTransactionsArgs, today: date) -> dict:
    """Up to ROW_CAP transactions in a period (optionally filtered by category), plus the true total count."""
    p = resolve(args.period, today)
    base = (
        select(Transaction, Category.name)
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(Category, Category.id == counted_category_id())  # rows awaiting review: no category
        .where(Account.user_id == user_id, Transaction.date.between(p.start, p.end))
    )
    if args.categories:
        base = base.where(Category.name.in_(args.categories))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    out_count = db.scalar(select(func.count()).select_from(base.where(Transaction.amount < 0).subquery()))
    page = db.execute(base.order_by(Transaction.date, Transaction.id).limit(config.AGENT_ROW_CAP)).all()
    cats_key = "+".join(sorted(args.categories)) if args.categories else "all"
    return {
        "period": period_json(p),
        "count_row": {"row_id": f"txncount:{p.key}:{cats_key}", "total_count": total,
                      "money_out_count": out_count},
        # raw_description is never selected into the output: merchant is the cleaned name
        "rows": [{"row_id": f"txn:{t.id}", "date": t.date.isoformat(), "amount": f"{t.amount:.2f}",
                  "merchant": t.merchant, "category": name} for t, name in page],
        "truncated": total > len(page),
        "coverage": coverage_row(db, user_id, p),
    }
