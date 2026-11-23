"""The one shared spending definition, used by tools, templates, verifier and (later) budgets.

Spending for a category = -(sum of signed amounts). A refund (a positive amount in a spending
category) lowers spending; if refunds exceed charges, spending is negative.
"""

from app.categories import DEFAULT_CATEGORIES

NON_SPENDING = {"Income", "Transfers"}
SPENDING_CATEGORIES: tuple[str, ...] = tuple(c for c in DEFAULT_CATEGORIES if c not in NON_SPENDING)
# Dining, Groceries, Rent, Utilities, Transport, Subscriptions, Shopping,
# Entertainment, Education, Health, Other   (11)


def counted_category_id():
    """SQL: the category a transaction counts under in answers; NULL while it waits in the review queue.

    An LLM guess below REVIEW_CONFIDENCE_THRESHOLD (or one code overruled) is a suggestion for the
    user, not a fact: answers report it with the uncategorized rows ("N transactions aren't
    categorized yet") instead of counting it silently. Uses the review queue's own rule, so the
    queue and the answers can't disagree about what is confirmed.
    """
    from sqlalchemy import case

    from app.models import Transaction
    from app.services.review import needs_review
    return case((needs_review(), None), else_=Transaction.category_id)
