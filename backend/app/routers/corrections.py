"""Saved rules: the user's category corrections per merchant (docs/specs/saved-rules.md).

A rule's transactions are the user's transactions from its merchant that are currently in its category, whatever
their source: resolving a merchant on the Review page marks its rows as set by hand, so the source alone can't say
which rows a rule is responsible for. The merchant's rows in other categories are never touched.
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.deps import CurrentUser, DbSession
from app.models import Account, Category, CategoryCorrection, CategorySource, Transaction, User
from app.schemas import CorrectionDeleteResult, CorrectionOut, CorrectionUpdate, CorrectionUpdateResult
from app.services.insights import safe_refresh_insights
from app.services.review import MIXED_MERCHANTS, apply_merchant_correction

router = APIRouter(prefix="/corrections", tags=["corrections"])


def _rule_rows(user: User, merchant: str, category_id: int) -> Select:
    """Ids of the user's transactions from `merchant` currently in `category_id`."""
    return (
        select(Transaction.id).join(Account, Account.id == Transaction.account_id)
        .where(Account.user_id == user.id, Transaction.merchant == merchant, Transaction.category_id == category_id)
    )


def _owned_rule(db: Session, user: User, rule_id: int) -> CategoryCorrection:
    """One of the user's rules; 404 for someone else's or a missing id, so its existence isn't revealed."""
    rule = db.scalar(select(CategoryCorrection).where(CategoryCorrection.id == rule_id,
                                                      CategoryCorrection.user_id == user.id))
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return rule


def _rule_out(db: Session, user: User, rule: CategoryCorrection) -> CorrectionOut:
    count = db.scalar(select(func.count()).select_from(_rule_rows(user, rule.merchant, rule.category_id).subquery()))
    return CorrectionOut(
        id=rule.id, merchant=rule.merchant, category=db.scalar(select(Category.name).where(Category.id == rule.category_id)),
        transaction_count=count, mixed=rule.merchant in MIXED_MERCHANTS,
    )


# GET /corrections
@router.get("", response_model=list[CorrectionOut])
def list_rules(user: CurrentUser, db: DbSession) -> list[CorrectionOut]:
    """Your saved rules, A→Z by merchant, with how many of your transactions each one currently covers."""
    rules = db.execute(
        select(CategoryCorrection, Category.name).join(Category, Category.id == CategoryCorrection.category_id)
        .where(CategoryCorrection.user_id == user.id).order_by(CategoryCorrection.merchant)
    ).all()
    counts = dict(db.execute(
        select(CategoryCorrection.id, func.count(Transaction.id))
        .join(Account, Account.user_id == CategoryCorrection.user_id)
        .join(Transaction, (Transaction.account_id == Account.id) & (Transaction.merchant == CategoryCorrection.merchant)
              & (Transaction.category_id == CategoryCorrection.category_id))
        .where(CategoryCorrection.user_id == user.id).group_by(CategoryCorrection.id)
    ).all())
    return [CorrectionOut(id=r.id, merchant=r.merchant, category=name, transaction_count=counts.get(r.id, 0),
                          mixed=r.merchant in MIXED_MERCHANTS) for r, name in rules]


# PATCH /corrections/{id}
@router.patch("/{rule_id}", response_model=CorrectionUpdateResult)
def change_rule(rule_id: int, body: CorrectionUpdate, user: CurrentUser, db: DbSession) -> CorrectionUpdateResult:
    """Change a rule's category: its transactions move to the new category, then the rule is applied as usual
    (the merchant's other rows not set by hand). Future imports use the new category."""
    rule = _owned_rule(db, user, rule_id)
    new_id = db.scalar(select(Category.id).where(Category.name == body.category))
    moved = 0
    if new_id != rule.category_id:
        ids = list(db.scalars(_rule_rows(user, rule.merchant, rule.category_id)))
        if ids:
            db.execute(
                update(Transaction).where(Transaction.id.in_(ids))
                .values(category_id=new_id, category_source=CategorySource.CORRECTION, category_confidence=1.0)
                .execution_options(synchronize_session=False)
            )
        moved = len(ids)
    also = apply_merchant_correction(db, user, rule.merchant, new_id)  # upserts the rule to new_id too
    safe_refresh_insights(db, user.id)  # categories changed: recurring series and flags can change
    db.commit()
    db.refresh(rule)
    return CorrectionUpdateResult(rule=_rule_out(db, user, rule), updated=moved + also)


# DELETE /corrections/{id}
@router.delete("/{rule_id}", response_model=CorrectionDeleteResult)
def remove_rule(rule_id: int, user: CurrentUser, db: DbSession, send_to_review: bool = False) -> CorrectionDeleteResult:
    """Remove a rule so future imports don't use it. Its transactions stay as they are, unless `send_to_review`
    is set: then they become uncategorized and appear in the review queue."""
    rule = _owned_rule(db, user, rule_id)
    sent = 0
    if send_to_review:
        ids = list(db.scalars(_rule_rows(user, rule.merchant, rule.category_id)))
        if ids:
            db.execute(
                update(Transaction).where(Transaction.id.in_(ids))
                .values(category_id=None, category_source=None, category_confidence=None)
                .execution_options(synchronize_session=False)
            )
        sent = len(ids)
    db.delete(rule)
    if sent:
        safe_refresh_insights(db, user.id)
    db.commit()
    return CorrectionDeleteResult(sent_to_review=sent)
