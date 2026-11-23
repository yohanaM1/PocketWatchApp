"""group_by_category and get_transactions against the fixtures/README.md oracle, plus scoping,
uncategorized and coverage."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.agent.periods import PeriodError, resolve
from app import config
from app.agent.plan import ExplicitRange, GetTransactionsArgs, GroupByCategoryArgs
from app.agent.spending import SPENDING_CATEGORIES
from app.agent.tools.spending import group_by_category
from app.agent.tools.transactions import get_transactions
from app.models import Category, Transaction
from tests.agent.helpers import FIXTURE_CSV, LABELS_CSV, make_fixture_user, subset
from tests.conftest import register

FULL = date(2026, 11, 5)  # the fixture's October runs to Oct 30, so oracle tests fix "today" after it

README = {  # fixtures/README.md, sum of amount
    "Dining": ("-61.87", "-82.54"), "Entertainment": ("-12.03", "0.00"), "Groceries": ("-161.39", "-105.16"),
    "Health": ("-10.00", "-10.00"), "Other": ("0.00", "-225.56"), "Rent": ("-925.00", "-925.00"),
    "Shopping": ("-489.60", "-62.09"), "Subscriptions": ("-24.46", "-40.41"), "Transport": ("-89.36", "-73.05"),
    "Utilities": ("-77.74", "-105.50"), "Education": ("0.00", "0.00"),
}


def run(db, user_id, period, categories=None, today=FULL):
    """Call the tool directly."""
    return group_by_category(db, user_id, GroupByCategoryArgs(period=period, categories=categories), today)


@pytest.mark.parametrize("month,col", [("2026-09", 0), ("2026-10", 1)])
def test_every_category_matches_readme(db, fixture_user, month, col):
    """Every spending category's net_amount equals the README, spending is its negation, and zero rows exist."""
    res = run(db, fixture_user[1], month)
    by = {r["category"]: r for r in res["rows"]}
    assert set(by) == set(SPENDING_CATEGORIES) and len(res["rows"]) == 11
    for cat, values in README.items():
        assert by[cat]["net_amount"] == values[col], cat
        assert Decimal(by[cat]["spending"]) == -Decimal(values[col])
        assert not by[cat]["spending"].startswith("-0")
        assert by[cat]["row_id"] == f"cat:{cat}:{month}"
    assert by["Education"]["count"] == 0


def test_zero_rows_and_no_income_or_transfers(db, fixture_user):
    """Entertainment in October and Other in September are zero rows; Income/Transfers never appear."""
    oct_, sep = run(db, fixture_user[1], "2026-10"), run(db, fixture_user[1], "2026-09")
    ent = next(r for r in oct_["rows"] if r["category"] == "Entertainment")
    assert (ent["net_amount"], ent["spending"], ent["count"]) == ("0.00", "0.00", 0)
    assert next(r for r in sep["rows"] if r["category"] == "Other")["count"] == 0
    assert not {"Income", "Transfers"} & {r["category"] for r in oct_["rows"]}


def test_totals_pin_the_spending_definition(db, fixture_user):
    """Sep total = all money out; Oct total excludes the -18.94 Venmo payment labeled Transfers."""
    assert run(db, fixture_user[1], "2026-09")["total"]["net_amount"] == "-1851.45"
    oct_total = run(db, fixture_user[1], "2026-10")["total"]
    assert oct_total["net_amount"] == "-1629.31" and oct_total["spending"] == "1629.31"


def test_rank(db, fixture_user):
    """Rent is rank 1 in both months; ties break by name."""
    for month in ("2026-09", "2026-10"):
        rows = run(db, fixture_user[1], month)["rows"]
        assert rows[0]["category"] == "Rent" and rows[0]["rank"] == 1
        zeros = [r["category"] for r in rows if r["spending"] == "0.00"]
        assert zeros == sorted(zeros)
        assert [r["rank"] for r in rows] == list(range(1, 12))


def test_uncategorized_money_out_only(db, fixture_user):
    """A cleared negative row moves to uncategorized; a cleared positive row is ignored."""
    _, user_id, account_id = fixture_user
    dining = db.scalar(select(Transaction).where(Transaction.account_id == account_id,
                                                 Transaction.amount == Decimal("-403.43")))  # Amazon, Shopping
    payroll = db.scalar(select(Transaction).where(Transaction.account_id == account_id, Transaction.amount > 0,
                                                  Transaction.date.between(date(2026, 9, 1), date(2026, 9, 30))))
    dining.category_id = payroll.category_id = None
    db.commit()
    res = run(db, user_id, "2026-09")
    shopping = next(r for r in res["rows"] if r["category"] == "Shopping")
    assert shopping["net_amount"] == f"{Decimal('-489.60') + Decimal('403.43'):.2f}"
    assert res["uncategorized"] == {"row_id": "uncat:2026-09", "net_amount": "-403.43",
                                    "spending": "403.43", "count": 1}


def test_all_uncategorized_without_labels(client, db):
    """Before categorization lands, every money-out row is uncategorized."""
    _, user_id, _ = make_fixture_user(client, db, "raw@umd.edu", labels=None)
    res = run(db, user_id, "2026-09")
    assert res["total"]["net_amount"] == "0.00"
    assert res["uncategorized"]["net_amount"] == "-1851.45"


def test_scoping(client, db, fixture_user):
    """A second user with the same fixture plus extra rows doesn't change user A's totals."""
    before = run(db, fixture_user[1], "2026-09")
    headers_b = register(client, "b@umd.edu")
    acct_b = client.get("/accounts", headers=headers_b).json()[0]["id"]
    extra = FIXTURE_CSV + b"2026-09-15,CHIPOTLE 1234,-500.00\n"
    client.post(f"/accounts/{acct_b}/imports", headers=headers_b, files={"file": ("t.csv", extra, "text/csv")})
    from app.devdata import apply_labels
    apply_labels(db, acct_b, FIXTURE_CSV, LABELS_CSV)
    db.commit()
    after = run(db, fixture_user[1], "2026-09")
    assert after == before and after["total"]["net_amount"] == "-1851.45"
    b = run(db, client.get("/auth/me", headers=headers_b).json()["id"], "2026-09")
    assert b["uncategorized"]["count"] == 1  # B's extra row is B's alone


def test_selection_row(db, fixture_user):
    """Two categories add a selection total; one category doesn't."""
    res = run(db, fixture_user[1], "2026-09", ["Groceries", "Dining"])
    assert res["selection"]["net_amount"] == "-223.26"
    assert res["selection"]["row_id"] == "sel:Dining+Groceries:2026-09"
    assert "selection" not in run(db, fixture_user[1], "2026-09", ["Dining"])


def test_coverage(client, db, fixture_user):
    """Coverage dates for the full fixture, an empty user, and a user with only September rows."""
    cov = run(db, fixture_user[1], "2026-09")["coverage"]
    assert (cov["first_date"], cov["last_date"]) == ("2026-09-01", "2026-10-30")
    assert cov["period_count"] > 0

    headers = register(client, "empty@umd.edu")
    empty_id = client.get("/auth/me", headers=headers).json()["id"]
    assert run(db, empty_id, "2026-09")["coverage"] == {"row_id": "coverage", "first_date": None,
                                                        "last_date": None, "period_count": 0}

    sep_csv, sep_labels = subset(FIXTURE_CSV, LABELS_CSV, lambda d: d.startswith("2026-09"))
    _, sep_id, _ = make_fixture_user(client, db, "sep@umd.edu", sep_csv, sep_labels)
    assert run(db, sep_id, "2026-10")["coverage"]["period_count"] == 0


def test_partial_month(db, fixture_user):
    """With today = Oct 19, 2026-10 resolves to Oct 1–19 and is partial."""
    res = run(db, fixture_user[1], "2026-10", today=date(2026, 10, 19))
    assert res["period"] == {"start": "2026-10-01", "end": "2026-10-19", "partial": True,
                             "label": "so far this month", "key": "2026-10-01_2026-10-19"}


def test_period_errors():
    """Future, too long, too early and reversed periods raise PeriodError with a reason."""
    today = date(2026, 10, 19)
    with pytest.raises(PeriodError) as e:
        resolve("2026-11", today)
    assert e.value.reason == "future"
    with pytest.raises(PeriodError) as e:
        resolve(ExplicitRange(start=date(2025, 1, 1), end=date(2026, 1, 2), explicit_reason="x"), today)
    assert e.value.reason == "too_long"
    with pytest.raises(PeriodError) as e:
        resolve("0000-01", today)
    assert e.value.reason == "too_early"
    with pytest.raises(PeriodError) as e:
        resolve(ExplicitRange(start=date(2026, 9, 2), end=date(2026, 9, 1), explicit_reason="x"), today)
    assert e.value.reason == "reversed"


def txns(db, user_id, period, categories=None, today=FULL):
    """Call get_transactions directly."""
    return get_transactions(db, user_id, GetTransactionsArgs(period=period, categories=categories), today)


def test_get_transactions_dining_count(db, fixture_user):
    """October Dining: 9 purchases, all money out; the row id names the period and categories."""
    res = txns(db, fixture_user[1], "2026-10", ["Dining"])
    assert res["count_row"] == {"row_id": "txncount:2026-10:Dining", "total_count": 9, "money_out_count": 9}
    assert len(res["rows"]) == 9 and {r["category"] for r in res["rows"]} == {"Dining"}
    assert not res["truncated"]


def test_get_transactions_spending_categories_skip_income_and_transfers(db, fixture_user):
    """No filter returns every row; the 11 spending categories leave out payroll and the Venmo payment."""
    everything = txns(db, fixture_user[1], "2026-10")
    assert everything["count_row"]["total_count"] == 32
    assert {"Income", "Transfers"} <= {r["category"] for r in everything["rows"]}
    spending = txns(db, fixture_user[1], "2026-10", list(SPENDING_CATEGORIES))
    assert spending["count_row"]["money_out_count"] == 27
    assert not {"Income", "Transfers"} & {r["category"] for r in spending["rows"]}


def test_get_transactions_refund_is_not_a_purchase(db, fixture_user):
    """A positive Dining row raises total_count but not money_out_count."""
    _, user_id, account_id = fixture_user
    dining_id = db.scalar(select(Category.id).where(Category.name == "Dining"))
    db.add(Transaction(account_id=account_id, date=date(2026, 10, 20), amount=Decimal("8.00"),
                       raw_description="REFUND", merchant="Refund", category_id=dining_id, import_hash="refund-1"))
    db.commit()
    row = txns(db, user_id, "2026-10", ["Dining"])["count_row"]
    assert (row["total_count"], row["money_out_count"]) == (10, 9)


def test_get_transactions_row_cap_and_no_raw_description(db, fixture_user, monkeypatch):
    """The row cap limits rows while total_count stays the full count; no raw_description ever leaves."""
    monkeypatch.setattr(config, "AGENT_ROW_CAP", 5)
    res = txns(db, fixture_user[1], "2026-09")
    assert len(res["rows"]) == 5 and res["count_row"]["total_count"] == 31 and res["truncated"]
    monkeypatch.setattr(config, "AGENT_ROW_CAP", 50)
    full = txns(db, fixture_user[1], "2026-09")
    assert all(set(r) == {"row_id", "date", "amount", "merchant", "category"} for r in full["rows"])
    ubers = [r for r in full["rows"] if r["date"] == "2026-09-08" and r["amount"] == "-25.14"]
    assert len(ubers) == 2  # the duplicate charge is two rows, not one


def test_get_transactions_scoping(client, db, fixture_user):
    """Another user's rows never show up in user A's count."""
    headers_b = register(client, "b@umd.edu")
    acct_b = client.get("/accounts", headers=headers_b).json()[0]["id"]
    client.post(f"/accounts/{acct_b}/imports", headers=headers_b, files={"file": ("t.csv", FIXTURE_CSV, "text/csv")})
    assert txns(db, fixture_user[1], "2026-09")["count_row"]["total_count"] == 31


# ---- LLM guesses awaiting review are not counted (they wait in the review queue) ----

def _amazon_as_llm_guess(db, account_id, confidence):
    """Mark the Sept Amazon charge (-403.43, Shopping) as an LLM guess with this confidence."""
    from app.models import CategorySource
    t = db.scalar(select(Transaction).where(Transaction.account_id == account_id,
                                            Transaction.amount == Decimal("-403.43")))
    t.category_source, t.category_confidence = CategorySource.LLM, confidence
    db.commit()


@pytest.mark.parametrize("confidence", [0.4, None])
def test_unconfirmed_guess_is_reported_as_uncategorized(db, fixture_user, confidence):
    """A guess below the threshold (or with no confidence) leaves Shopping and shows up in the uncategorized row."""
    _, user_id, account_id = fixture_user
    before = txns(db, user_id, "2026-09", ["Shopping"])["count_row"]["money_out_count"]
    _amazon_as_llm_guess(db, account_id, confidence)
    res = run(db, user_id, "2026-09")
    shopping = next(r for r in res["rows"] if r["category"] == "Shopping")
    assert shopping["net_amount"] == f"{Decimal('-489.60') + Decimal('403.43'):.2f}"
    assert res["uncategorized"]["net_amount"] == "-403.43" and res["uncategorized"]["count"] == 1
    assert txns(db, user_id, "2026-09", ["Shopping"])["count_row"]["money_out_count"] == before - 1


def test_confident_guess_is_counted(db, fixture_user):
    """A guess at the threshold counts in its category like any other."""
    _, user_id, account_id = fixture_user
    _amazon_as_llm_guess(db, account_id, config.REVIEW_CONFIDENCE_THRESHOLD)
    res = run(db, user_id, "2026-09")
    assert next(r for r in res["rows"] if r["category"] == "Shopping")["net_amount"] == "-489.60"
    assert res["uncategorized"]["count"] == 0


def test_threshold_setting_moves_the_line(db, fixture_user, monkeypatch):
    """Answers follow REVIEW_CONFIDENCE_THRESHOLD, the same setting as the review queue."""
    _, user_id, account_id = fixture_user
    _amazon_as_llm_guess(db, account_id, 0.6)
    assert run(db, user_id, "2026-09")["uncategorized"]["count"] == 1
    monkeypatch.setattr(config, "REVIEW_CONFIDENCE_THRESHOLD", 0.5)
    assert run(db, user_id, "2026-09")["uncategorized"]["count"] == 0
