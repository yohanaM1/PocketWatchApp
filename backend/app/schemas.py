from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.categories import DEFAULT_CATEGORIES
from app.models import AccountType, AnomalyKind, AnomalyStatus, CategorySource


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AccountCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    type: AccountType


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: AccountType


class ImportResult(BaseModel):
    total_rows: int
    imported: int
    skipped_duplicates: int
    categorized: int = 0  # new rows given a category, by a saved correction or a confident LLM guess
    needs_review: int = 0  # new rows still uncategorized (the LLM was unsure or unavailable)


class TransactionOut(BaseModel):
    id: int
    account_id: int
    account_name: str
    date: date
    amount: str  # 2-decimal string, so money never passes through a float
    merchant: str | None
    description: str
    category: str | None
    category_source: CategorySource | None
    category_confidence: float | None  # 1.0 for user/correction; the LLM's own score for an llm guess
    in_review: bool  # waiting in the review queue: `category` is only a guess, not counted in budgets or answers


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int
    # Totals over every page of the filtered set, as positive 2-decimal strings
    money_out: str
    money_in: str
    uncategorized_count: int
    review_count: int  # rows waiting in the review queue: uncategorized plus low-confidence guesses


class CategoryUpdate(BaseModel):
    category: Literal[tuple(DEFAULT_CATEGORIES)]  # subscripting with a tuple = Literal["Dining", ...]
    apply_to_merchant: bool = False


class CategoryUpdateResult(BaseModel):
    transaction: TransactionOut
    also_updated: int  # other transactions changed because apply_to_merchant was set
    same_merchant_count: int  # other same-merchant transactions not set by hand (what "apply to all" would change)


class ReviewGuess(BaseModel):
    category: str
    count: int  # queued rows in the group with this guess
    confidence: float | None  # the lowest confidence among them; None if the pipeline gave none


class ReviewGroup(BaseModel):
    merchant: str | None  # always set for imported rows; None only for rows stored without one
    count: int
    reason: Literal["uncategorized", "low_confidence"]  # uncategorized if any row has no category
    guesses: list[ReviewGuess]  # most common first; empty when nothing was guessed
    suggestion: str | None  # guesses[0].category, used to preset the picker
    mixed: bool  # handle one transaction at a time, and don't remember by default
    mixed_reason: Literal["known_mixed", "guesses_differ", "history_differs"] | None
    money_out: str  # positive 2-decimal strings, as on TransactionPage
    money_in: str
    first_date: date
    last_date: date
    examples: list[TransactionOut]  # up to 3, newest first


class ReviewPage(BaseModel):
    groups: list[ReviewGroup]
    total_groups: int
    total_transactions: int


class ReviewResolve(BaseModel):
    merchant: str | None
    category: Literal[tuple(DEFAULT_CATEGORIES)]
    remember: bool = True  # save a correction for the merchant and apply it to its other rows not set by hand


class ReviewResolveResult(BaseModel):
    updated: int  # queued rows set to the category
    also_updated: int  # other rows changed by the saved correction (0 without remember)


class RecurringSeriesOut(BaseModel):
    key: str  # stable across rebuilds; use it, not a database id
    merchant: str
    category: str | None  # the category of the series' latest charge
    amount: str  # the current amount, 2-decimal string, negative = money out
    previous_amount: str | None  # before the most recent price change
    amount_changed_on: date | None
    interval_days: int
    first_date: date
    last_date: date
    next_expected: date | None  # null for a stopped series
    charge_count: int
    active: bool


class RecurringPage(BaseModel):
    as_of: date | None  # the latest transaction date; "active" and "next expected" are relative to it, not today
    monthly_total: str  # the active series' current amounts added up, negative
    active_count: int
    series: list[RecurringSeriesOut]  # active first (largest first), then stopped


class FlagOut(BaseModel):
    id: int
    kind: AnomalyKind
    reason: str
    status: AnomalyStatus
    created_at: datetime
    transaction: TransactionOut


class FlagPage(BaseModel):
    items: list[FlagOut]
    open_count: int  # for the nav badge, whatever the status filter


class FlagUpdate(BaseModel):
    status: AnomalyStatus


MonthStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]  # YYYY-MM


class BudgetRowOut(BaseModel):
    category: str
    budget_id: int | None  # None when the category has no budget this month
    limit: str | None
    spent: str  # the shared spending definition; review-queue guesses aren't counted
    left: str | None  # limit - spent, negative when over; None without a budget
    percent: int | None  # spent / limit x 100, rounded; can pass 100 or be negative after refunds
    status: Literal["ok", "near", "over", "none"]  # none = no budget
    previous_spent: str | None  # the month before; None when nothing was imported for that month


class BudgetPage(BaseModel):
    month: str  # YYYY-MM
    as_of: date | None  # the latest transaction date
    max_month: str  # the later of as_of's month and today's month; the page's › arrow stops here
    budgeted: str  # the totals cover budgeted categories only
    spent: str
    left: str
    over_count: int
    budget_count: int
    rows: list[BudgetRowOut]  # all 11 spending categories, in the fixed category order
    pending_review_count: int  # transactions in the month held back from spending until reviewed
    previous_month_count: int  # budgets in the month before, for the "fill blanks" button


class BudgetSet(BaseModel):
    limit: Annotated[Decimal, Field(gt=0, le=1_000_000, decimal_places=2)]


class BudgetCopy(BaseModel):
    from_month: MonthStr
    to_month: MonthStr


class BudgetCopyResult(BaseModel):
    copied: int
    skipped: int  # categories to_month already had


class CorrectionOut(BaseModel):
    id: int
    merchant: str
    category: str
    transaction_count: int  # your transactions from the merchant currently in the rule's category
    mixed: bool  # a multi-purpose merchant (the review queue's MIXED_MERCHANTS), where one category is risky


class CorrectionUpdate(BaseModel):
    category: Literal[tuple(DEFAULT_CATEGORIES)]


class CorrectionUpdateResult(BaseModel):
    rule: CorrectionOut
    updated: int  # transactions whose category changed


class CorrectionDeleteResult(BaseModel):
    sent_to_review: int  # transactions made uncategorized (0 unless send_to_review was set)
