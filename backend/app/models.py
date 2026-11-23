"""Database tables for PocketWatch.

Conventions:
- Money is DECIMAL(12, 2), never float, so the verifier can compare numbers exactly.
- Transaction amounts are signed: negative = money out, positive = money in.
- Budget months are stored as the first day of the month (e.g. 2026-10-01).
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

Money = Numeric(12, 2)


class Base(DeclarativeBase):
    pass


# MySQL ENUM type helper for the Python enums below
def _enum(cls: type[enum.Enum]) -> Enum:
    """Build a MySQL ENUM column type from a Python Enum, storing the lowercase values."""
    return Enum(cls, values_callable=lambda e: [m.value for m in e], native_enum=True)


class AccountType(enum.Enum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CREDIT = "credit"


class CategorySource(enum.Enum):
    CORRECTION = "correction"  # reused from the user's past fix for this merchant
    LLM = "llm"  # guessed by the categorization pipeline
    USER = "user"  # set by hand in the UI


class AnomalyStatus(enum.Enum):
    OPEN = "open"
    DISMISSED = "dismissed"  # the user said it looks fine
    CONFIRMED = "confirmed"  # the user agreed something is wrong; nothing else changes


class AnomalyKind(enum.Enum):
    DUPLICATE = "duplicate"  # same account, date, merchant and amount as an earlier row
    LARGE_CHARGE = "large_charge"  # far above the merchant's (or category's) usual charge
    UNFAMILIAR_MERCHANT = "unfamiliar_merchant"  # the only row ever from this merchant, and large


class VerificationStatus(enum.Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"  # verifier rejected the numbers even after a retry
    FAILED = "failed"  # agent loop errored before producing an answer


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    accounts: Mapped[list["Account"]] = relationship(back_populates="user")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[AccountType] = mapped_column(_enum(AccountType))

    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_account_date", "account_id", "date"),
        # import_hash = hash(date, amount, raw_description, nth occurrence in the file),
        # so identical rows in one file are both kept, but re-importing a file is a no-op.
        UniqueConstraint("account_id", "import_hash", name="uq_transactions_account_import_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    raw_description: Mapped[str] = mapped_column(String(255))
    merchant: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)
    category_confidence: Mapped[float | None] = mapped_column(Float)
    category_source: Mapped[CategorySource | None] = mapped_column(_enum(CategorySource))
    import_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Set by refresh_insights when this charge is part of a detected recurring series.
    recurring_series_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_series.id", ondelete="SET NULL"), index=True
    )

    account: Mapped[Account] = relationship(back_populates="transactions")
    category: Mapped[Category | None] = relationship()
    anomaly_flags: Mapped[list["AnomalyFlag"]] = relationship(back_populates="transaction")


class CategoryCorrection(Base):
    """A user's fix for a merchant, reused to categorize future imports."""

    __tablename__ = "category_corrections"
    __table_args__ = (UniqueConstraint("user_id", "merchant", name="uq_corrections_user_merchant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    merchant: Mapped[str] = mapped_column(String(100))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))


class RecurringSeries(Base):
    """A detected recurring charge. Derived data: refresh_insights deletes and rebuilds a user's rows."""

    __tablename__ = "recurring_series"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_recurring_series_user_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # MERCHANT:first-charge-date:first-amount, stable across rebuilds; APIs and chat slots use it, never `id`.
    key: Mapped[str] = mapped_column(String(150))
    merchant: Mapped[str] = mapped_column(String(100))
    typical_amount: Mapped[Decimal] = mapped_column(Money)  # the current amount (negative = money out)
    interval_days: Mapped[int] = mapped_column(Integer)
    first_date: Mapped[date] = mapped_column(Date)
    last_date: Mapped[date] = mapped_column(Date)
    charge_count: Mapped[int] = mapped_column(Integer)
    previous_amount: Mapped[Decimal | None] = mapped_column(Money)  # before the most recent price change
    amount_changed_on: Mapped[date | None] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean)


class AnomalyFlag(Base):
    """A transaction flagged as unusual. Re-runs only add flags; only the user changes `status`."""

    __tablename__ = "anomaly_flags"
    __table_args__ = (UniqueConstraint("transaction_id", "kind", name="uq_anomaly_flags_transaction_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[AnomalyKind] = mapped_column(_enum(AnomalyKind))
    reason: Mapped[str] = mapped_column(String(255))
    status: Mapped[AnomalyStatus] = mapped_column(
        _enum(AnomalyStatus), default=AnomalyStatus.OPEN, server_default=AnomalyStatus.OPEN.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    transaction: Mapped[Transaction] = relationship(back_populates="anomaly_flags")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", "month", name="uq_budgets_user_category_month"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    month: Mapped[date] = mapped_column(Date)
    limit_amount: Mapped[Decimal] = mapped_column(Money)


class QueryLog(Base):
    """Audit log of every question asked and the AI's answer."""

    __tablename__ = "queries"
    __table_args__ = (Index("ix_queries_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSON)  # every tool call with args and results, in order
    answer: Mapped[str | None] = mapped_column(Text)  # template text only; NULL when unverified
    verification_status: Mapped[VerificationStatus] = mapped_column(_enum(VerificationStatus))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    plan: Mapped[dict | None] = mapped_column(JSON)  # validated plan + as_of + resolved period
    extra_sentence: Mapped[str | None] = mapped_column(Text)  # unverified LLM sentence, if any
    slots: Mapped[list | None] = mapped_column(JSON)  # (start, end, row_id, field, kind) per number
    final_results: Mapped[list | None] = mapped_column(JSON)  # the final-run tool results the answer uses
    failure_reason: Mapped[str | None] = mapped_column(String(500))  # never returned by the API
    attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    llm_model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    as_of: Mapped[date | None] = mapped_column(Date)  # the ET date periods were resolved against
    latency_ms: Mapped[int | None] = mapped_column(Integer)
