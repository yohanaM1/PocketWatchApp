"""Recurring payments and anomaly flags (design: docs/specs/recurring-and-anomalies.md).

`detect_recurring` and `detect_anomalies` are pure functions over plain rows, with no database, so the rules can be
tested on hand-built lists. `refresh_insights` (below them) loads a user's rows, runs both and stores the results.
"""

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import config
from app.models import Account, AnomalyFlag, AnomalyKind, Category, RecurringSeries, Transaction

logger = logging.getLogger(__name__)

# Monthly only (decision 7): consecutive charges 26-35 days apart; the next one is looked for near +30 days.
MIN_GAP_DAYS, MAX_GAP_DAYS, TARGET_GAP_DAYS = 26, 35, 30
PRICE_CHANGE_MAX = Decimal("0.20")  # a new amount within +/-20% of the current one may be a price change
ACTIVE_GRACE_DAYS = 7  # active if the next charge is at most this late, relative to the latest transaction
LARGE_MIN_OTHERS = 3  # a usual charge needs at least this many other charges to compare with
UNFAMILIAR_MIN_HISTORY_DAYS = 30  # a first import isn't full of "new merchant" flags
NOT_COMPARED = {"Transfers"}  # money moved between your own accounts is never an unusually large "charge"


@dataclass(frozen=True)
class Row:
    """One transaction, as the rules see it. `amount` is signed: negative = money out."""

    id: int
    account_id: int
    date: date
    amount: Decimal
    merchant: str | None
    category: str | None = None


@dataclass(frozen=True)
class SeriesResult:
    key: str
    merchant: str
    amount: Decimal  # the current amount, negative
    interval_days: int
    first_date: date
    last_date: date
    charge_count: int
    previous_amount: Decimal | None
    amount_changed_on: date | None
    active: bool
    transaction_ids: tuple[int, ...]

    @property
    def next_expected(self) -> date:
        """The last charge plus the series' interval."""
        return self.last_date + timedelta(days=self.interval_days)


@dataclass(frozen=True)
class FlagResult:
    transaction_id: int
    kind: AnomalyKind
    reason: str


def _gap_ok(a: Row, b: Row) -> bool:
    """b falls in the monthly window after a."""
    return MIN_GAP_DAYS <= (b.date - a.date).days <= MAX_GAP_DAYS


def _closeness(last: Row, r: Row) -> tuple[int, int]:
    """Sort key: nearest to last + 30 days, then earliest id."""
    return abs((r.date - last.date).days - TARGET_GAP_DAYS), r.id


def _price_change_ok(current: Decimal, new: Decimal) -> bool:
    """A different amount within +/-20% of the current one."""
    return new != current and abs(new - current) <= abs(current) * PRICE_CHANGE_MAX


def _chains(charges: list[Row]) -> list[tuple[list[Row], Decimal | None, date | None]]:
    """Recurring chains in one merchant's money-out charges (sorted by date, then id).

    Each chain is (charges, previous amount, date of the most recent price change).
    """
    used: set[int] = set()
    most_recent = charges[-1].id if charges else None
    chains = []
    for i, seed in enumerate(charges):
        if seed.id in used:
            continue
        # Start: a second charge in the window at exactly the same amount.
        starts = [r for r in charges[i + 1:] if r.id not in used and r.amount == seed.amount and _gap_ok(seed, r)]
        if not starts:
            continue
        chain = [seed, min(starts, key=lambda r: _closeness(seed, r))]
        current, previous, changed_on = seed.amount, None, None
        while True:
            last = chain[-1]
            window = [r for r in charges if r.id not in used and r not in chain and r.date > last.date
                      and _gap_ok(last, r)]
            same = [r for r in window if r.amount == current]
            if same:
                chain.append(min(same, key=lambda r: _closeness(last, r)))
                continue
            # A price change counts only if the next charge repeats it, or it's the merchant's latest charge.
            change = None
            for r in sorted((r for r in window if _price_change_ok(current, r.amount)),
                            key=lambda r: _closeness(last, r)):
                repeats = any(n.id not in used and n is not r and n.amount == r.amount and _gap_ok(r, n)
                              for n in charges)
                if repeats or r.id == most_recent:
                    change = r
                    break
            if change is None:
                break
            previous, changed_on, current = current, change.date, change.amount
            chain.append(change)
        # An everyday merchant (coffee, groceries) repeats a price by chance. In a real series, the series' charges
        # are most of what the merchant charged over that time.
        span = [r for r in charges if chain[0].date <= r.date <= chain[-1].date]
        if len(chain) * 2 < len(span):
            continue
        used.update(r.id for r in chain)
        chains.append((chain, previous, changed_on))
    return chains


def detect_recurring(rows: list[Row]) -> list[SeriesResult]:
    """Monthly series of money-out charges per merchant, judged active against the latest transaction date."""
    if not rows:
        return []
    as_of = max(r.date for r in rows)
    by_merchant: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        if r.amount < 0 and r.merchant:
            by_merchant[r.merchant].append(r)

    out: list[SeriesResult] = []
    keys: set[str] = set()
    for merchant in sorted(by_merchant):
        charges = sorted(by_merchant[merchant], key=lambda r: (r.date, r.id))
        for chain, previous, changed_on in _chains(charges):
            gaps = [(b.date - a.date).days for a, b in zip(chain, chain[1:])]
            interval = int(round(statistics.median(gaps)))
            first, last = chain[0], chain[-1]
            key = f"{merchant}:{first.date.isoformat()}:{-first.amount:.2f}"
            n = 2
            while key in keys:  # only if two chains start on the same day at the same amount
                key, n = f"{merchant}:{first.date.isoformat()}:{-first.amount:.2f}:{n}", n + 1
            keys.add(key)
            out.append(SeriesResult(
                key=key, merchant=merchant, amount=last.amount, interval_days=interval,
                first_date=first.date, last_date=last.date, charge_count=len(chain),
                previous_amount=previous, amount_changed_on=changed_on,
                active=last.date + timedelta(days=interval + ACTIVE_GRACE_DAYS) >= as_of,
                transaction_ids=tuple(r.id for r in chain),
            ))
    return out


def _times(amount: Decimal, base: Decimal) -> int:
    """How many times larger, rounded down ("About 6x"); at least the threshold, since only flags use it."""
    return int(amount / base)


def detect_anomalies(rows: list[Row], series: list[SeriesResult]) -> list[FlagResult]:
    """Duplicates, unusually large charges and large first-time merchants (one flag per row and kind)."""
    if not rows:
        return []
    in_series = {tid for s in series for tid in s.transaction_ids}
    out_rows = [r for r in rows if r.amount < 0 and r.merchant]
    flags: list[FlagResult] = []

    # Duplicates: every row after the first with the same account, date, merchant and amount.
    groups: dict[tuple, list[Row]] = defaultdict(list)
    for r in out_rows:
        groups[(r.account_id, r.date, r.merchant, r.amount)].append(r)
    for group in groups.values():
        for r in sorted(group, key=lambda r: r.id)[1:]:
            flags.append(FlagResult(r.id, AnomalyKind.DUPLICATE,
                                    "Same date, merchant and amount as another charge in this account"))

    # Large charges: against the merchant's usual charge, or the category's when the merchant has too few.
    by_merchant: dict[str, list[Row]] = defaultdict(list)
    by_category: dict[str, list[Row]] = defaultdict(list)
    for r in out_rows:
        by_merchant[r.merchant].append(r)
        if r.category:
            by_category[r.category].append(r)
    for r in out_rows:
        if r.id in in_series or not r.category or r.category in NOT_COMPARED:
            continue
        others = [-o.amount for o in by_merchant[r.merchant] if o.id != r.id]
        usual_of, multiple = r.merchant, config.ANOMALY_LARGE_MULTIPLE
        if len(others) < LARGE_MIN_OTHERS:
            others = [-o.amount for o in by_category[r.category] if o.id != r.id]
            usual_of, multiple = r.category, config.ANOMALY_LARGE_CATEGORY_MULTIPLE
        if len(others) < LARGE_MIN_OTHERS:
            continue
        base = statistics.median(others)
        if base > 0 and -r.amount >= multiple * base:
            flags.append(FlagResult(r.id, AnomalyKind.LARGE_CHARGE,
                                    f"About {_times(-r.amount, base)}× your usual {usual_of} charge"))

    # Unfamiliar merchants: the only row ever from the merchant (refunds count as having dealt with them),
    # large, and with enough of the user's history before it that "never seen" means something.
    seen: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.merchant:
            seen[r.merchant] += 1
    history_start = min(r.date for r in rows)
    minimum = config.ANOMALY_UNFAMILIAR_MIN
    for r in out_rows:
        if seen[r.merchant] == 1 and -r.amount >= minimum \
                and (r.date - history_start).days >= UNFAMILIAR_MIN_HISTORY_DAYS:
            flags.append(FlagResult(r.id, AnomalyKind.UNFAMILIAR_MERCHANT,
                                    f"First charge from this merchant, and over ${minimum:,.0f}"))
    return flags


# --- Storage ----------------------------------------------------------------------------------------------------

def load_rows(db: Session, user_id: int) -> list[Row]:
    """Every transaction of the user, across their accounts, as rule input."""
    result = db.execute(
        select(Transaction.id, Transaction.account_id, Transaction.date, Transaction.amount, Transaction.merchant,
               Category.name)
        .join(Account, Account.id == Transaction.account_id)
        .outerjoin(Category, Category.id == Transaction.category_id)
        .where(Account.user_id == user_id)
    )
    return [Row(*r) for r in result]


def refresh_insights(db: Session, user_id: int) -> dict[str, int]:
    """Rebuild the user's recurring series and add any new anomaly flags. Does not commit.

    Series are derived data: deleted and rebuilt (their keys stay stable). Flags are only ever added; an existing
    flag keeps its status even when its rule no longer matches, and only the user changes it.
    """
    rows = load_rows(db, user_id)
    series = detect_recurring(rows)
    flags = detect_anomalies(rows, series)
    txn_ids = [r.id for r in rows]

    if txn_ids:
        db.execute(update(Transaction).where(Transaction.id.in_(txn_ids)).values(recurring_series_id=None)
                   .execution_options(synchronize_session=False))
    db.execute(delete(RecurringSeries).where(RecurringSeries.user_id == user_id)
               .execution_options(synchronize_session=False))
    for s in series:
        stored = RecurringSeries(
            user_id=user_id, key=s.key, merchant=s.merchant, typical_amount=s.amount, interval_days=s.interval_days,
            first_date=s.first_date, last_date=s.last_date, charge_count=s.charge_count,
            previous_amount=s.previous_amount, amount_changed_on=s.amount_changed_on, active=s.active,
        )
        db.add(stored)
        db.flush()
        db.execute(update(Transaction).where(Transaction.id.in_(s.transaction_ids))
                   .values(recurring_series_id=stored.id).execution_options(synchronize_session=False))

    existing = set(db.execute(
        select(AnomalyFlag.transaction_id, AnomalyFlag.kind).where(AnomalyFlag.transaction_id.in_(txn_ids))
    ).all()) if txn_ids else set()
    new = [f for f in flags if (f.transaction_id, f.kind) not in existing]
    db.add_all(AnomalyFlag(transaction_id=f.transaction_id, kind=f.kind, reason=f.reason) for f in new)
    db.flush()
    return {"series": len(series), "new_flags": len(new)}


def safe_refresh_insights(db: Session, user_id: int) -> None:
    """refresh_insights in a savepoint: a detection error is logged and never fails the caller's request.

    Results catch up on the next import, category change or `python -m scripts.refresh_insights`.
    """
    try:
        with db.begin_nested():
            refresh_insights(db, user_id)
    except Exception:
        logger.exception("refresh_insights failed for user %s", user_id)
