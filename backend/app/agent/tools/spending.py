"""group_by_category: spending per category for one user and period, plus total, selection and coverage rows."""

from datetime import date
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.agent.periods import ResolvedPeriod, period_json, resolve
from app.agent.plan import GroupByCategoryArgs
from app.agent.spending import SPENDING_CATEGORIES, counted_category_id
from app.models import Account, Category, Transaction

ZERO = Decimal("0.00")


def _spending(net: Decimal) -> str:
    """-(net) as a 2-decimal string; ZERO - net so a zero row is "0.00", never "-0.00"."""
    return f"{ZERO - net:.2f}"


def group_by_category(db: Session, user_id: int, args: GroupByCategoryArgs, today: date) -> dict:
    """Spending per spending category in a period, with zero rows, a total row and uncategorized money out."""
    p = resolve(args.period, today)
    money_out = case((Transaction.amount < 0, Transaction.amount), else_=0)
    category = counted_category_id().label("category_id")  # NULL while awaiting review
    stmt = (
        select(
            category,
            func.coalesce(func.sum(Transaction.amount), 0),
            func.count(),
            func.coalesce(func.sum(money_out), 0),
            func.sum(case((Transaction.amount < 0, 1), else_=0)),
        )
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id, Transaction.date.between(p.start, p.end))
        .group_by(category)
    )
    names = dict(db.execute(select(Category.id, Category.name)).all())
    net_by_name: dict[str, tuple[Decimal, int]] = {}
    uncat_net, uncat_count = ZERO, 0
    for cat_id, net, count, out_sum, out_count in db.execute(stmt):
        if cat_id is None:
            uncat_net, uncat_count = Decimal(out_sum), int(out_count or 0)
        else:
            net_by_name[names[cat_id]] = (Decimal(net), int(count))

    rows = []
    for name in SPENDING_CATEGORIES:
        net, count = net_by_name.get(name, (ZERO, 0))
        rows.append({"row_id": f"cat:{name}:{p.key}", "category": name,
                     "net_amount": f"{net:.2f}", "spending": _spending(net), "count": count})
    rows.sort(key=lambda r: (-Decimal(r["spending"]), r["category"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    total_net = sum((Decimal(r["net_amount"]) for r in rows), ZERO)
    result = {
        "period": period_json(p),
        "rows": rows,
        "total": {"row_id": f"total:{p.key}", "net_amount": f"{total_net:.2f}",
                  "spending": _spending(total_net), "count": sum(r["count"] for r in rows)},
        "uncategorized": {"row_id": f"uncat:{p.key}", "net_amount": f"{uncat_net:.2f}",
                          "spending": _spending(uncat_net), "count": uncat_count},
        "coverage": coverage_row(db, user_id, p),
    }
    if args.categories and len(args.categories) > 1:  # verifiable sum for "food" = Dining + Groceries
        picked = sorted(set(args.categories))
        sel_net = sum((Decimal(r["net_amount"]) for r in rows if r["category"] in picked), ZERO)
        result["selection"] = {"row_id": f"sel:{'+'.join(picked)}:{p.key}", "categories": picked,
                               "net_amount": f"{sel_net:.2f}", "spending": _spending(sel_net),
                               "count": sum(r["count"] for r in rows if r["category"] in picked)}
    return result


def coverage_row(db: Session, user_id: int, p: ResolvedPeriod) -> dict:
    """What the user's imports cover: first/last transaction date overall, and how many fall in the period."""
    first, last, in_period = db.execute(
        select(func.min(Transaction.date), func.max(Transaction.date),
               func.coalesce(func.sum(case((Transaction.date.between(p.start, p.end), 1), else_=0)), 0))
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user_id)
    ).one()
    return {"row_id": "coverage", "first_date": first.isoformat() if first else None,
            "last_date": last.isoformat() if last else None, "period_count": int(in_period)}
