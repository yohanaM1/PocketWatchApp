"""compare_periods: spending per category in two periods, the change, and the largest transactions behind the biggest changes."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.periods import PeriodError, ResolvedPeriod, resolve
from app.agent.plan import ComparePeriodsArgs, GroupByCategoryArgs
from app.agent.spending import SPENDING_CATEGORIES, counted_category_id
from app.agent.tools.spending import group_by_category
from app.models import Account, Category, Transaction

ZERO = Decimal("0.00")
TOP_MOVERS = 3  # categories that get drivers
DRIVERS_PER_PERIOD = 3


def _delta(row_id: str, base_spending: str, current_spending: str) -> dict:
    """One change row; ZERO + keeps a zero change "0.00", never "-0.00"."""
    change = ZERO + (Decimal(current_spending) - Decimal(base_spending))
    direction = "up" if change > 0 else "down" if change < 0 else "same"
    return {"row_id": row_id, "base_spending": base_spending, "current_spending": current_spending,
            "change": f"{change:.2f}", "direction": direction}


def _largest(db: Session, user_id: int, category: str, p: ResolvedPeriod) -> list[dict]:
    """Up to DRIVERS_PER_PERIOD money-out transactions in one category and period, largest first."""
    rows = db.execute(
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .join(Category, Category.id == counted_category_id())
        .where(Account.user_id == user_id, Category.name == category, Transaction.amount < 0,
               Transaction.date.between(p.start, p.end))
        .order_by(Transaction.amount, Transaction.id)
        .limit(DRIVERS_PER_PERIOD)
    ).scalars()
    # same row shape as get_transactions; raw_description is never selected into the output
    return [{"row_id": f"txn:{t.id}", "date": t.date.isoformat(), "amount": f"{t.amount:.2f}",
             "merchant": t.merchant, "category": category} for t in rows]


def compare_periods(db: Session, user_id: int, args: ComparePeriodsArgs, today: date) -> dict:
    """Spending per category in `base` and `current` (base must come first), the change, and drivers."""
    base_p, cur_p = resolve(args.base, today), resolve(args.current, today)
    if base_p.end >= cur_p.start:
        raise PeriodError("order", "base must end before current starts")
    base = group_by_category(db, user_id, GroupByCategoryArgs(period=args.base, categories=args.categories), today)
    current = group_by_category(db, user_id, GroupByCategoryArgs(period=args.current, categories=args.categories), today)
    pair = f"{base_p.key}:{cur_p.key}"

    base_by = {r["category"]: r for r in base["rows"]}
    cur_by = {r["category"]: r for r in current["rows"]}
    deltas = [{"category": c, **_delta(f"delta:{c}:{pair}", base_by[c]["spending"], cur_by[c]["spending"])}
              for c in (args.categories or SPENDING_CATEGORIES)]
    deltas.sort(key=lambda d: (-abs(Decimal(d["change"])), d["category"]))
    for i, d in enumerate(deltas, 1):
        d["rank"] = i

    result = {
        "base": base,
        "current": current,
        "deltas": deltas,
        "total_delta": _delta(f"delta:total:{pair}", base["total"]["spending"], current["total"]["spending"]),
        "uncategorized_delta": _delta(f"delta:uncat:{pair}", base["uncategorized"]["spending"],
                                      current["uncategorized"]["spending"]),
        "drivers": [{"category": d["category"], "base": _largest(db, user_id, d["category"], base_p),
                     "current": _largest(db, user_id, d["category"], cur_p)}
                    for d in deltas if d["direction"] != "same"][:TOP_MOVERS],
        "days": {"base": (base_p.end - base_p.start).days + 1, "current": (cur_p.end - cur_p.start).days + 1},
    }
    if "selection" in base:  # 2+ categories requested
        picked = "+".join(base["selection"]["categories"])
        result["selection_delta"] = _delta(f"delta:sel:{picked}:{pair}", base["selection"]["spending"],
                                           current["selection"]["spending"])
    return result
