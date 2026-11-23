"""Known-good template cases shared by the template, verifier, tampered-case and UI-contract tests.

Each Case has a hand-built FinalPlan, a hand-built tool result (no database needed) and the exact
expected text. Adding a template branch means adding one case here.
"""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from app.agent.loop import list_plan_json, plan_json
from app.agent.periods import period_json, resolve
from app.agent.plan import ExplicitRange, FinalPlan, ListPlan
from app.agent.spending import SPENDING_CATEGORIES
from app.agent.templates import (render_category_total, render_flag_list, render_recurring_list,
                                 render_transaction_count)

TODAY = date(2026, 10, 19)
NOV5 = date(2026, 11, 5)  # after the fixture's October, so "in October" is the whole month
SEP = {"Dining": ("-61.87", 7), "Entertainment": ("-12.03", 1), "Groceries": ("-161.39", 6),
       "Health": ("-10.00", 1), "Rent": ("-925.00", 1), "Shopping": ("-489.60", 4),
       "Subscriptions": ("-24.46", 3), "Transport": ("-89.36", 5), "Utilities": ("-77.74", 2)}


@dataclass
class Case:
    name: str
    question: str
    plan: FinalPlan | ListPlan
    result: dict
    today: date
    expected_text: str


def result(period, today, nets: dict, *, uncat=("0.00", 0), coverage=("2026-09-01", "2026-10-17", 30),
           selection: list[str] | None = None) -> dict:
    """A group_by_category-shaped result: 11 rows (zero rows filled in), total, uncategorized, coverage."""
    p = resolve(period, today)
    rows = []
    for name in SPENDING_CATEGORIES:
        net, count = nets.get(name, ("0.00", 0))
        rows.append({"row_id": f"cat:{name}:{p.key}", "category": name, "net_amount": net,
                     "spending": f"{Decimal('0.00') - Decimal(net):.2f}", "count": count})
    rows.sort(key=lambda r: (-Decimal(r["spending"]), r["category"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    total = sum((Decimal(r["net_amount"]) for r in rows), Decimal("0.00"))
    out = {
        "period": period_json(p),
        "rows": rows,
        "total": {"row_id": f"total:{p.key}", "net_amount": f"{total:.2f}",
                  "spending": f"{Decimal('0.00') - total:.2f}", "count": sum(r["count"] for r in rows)},
        "uncategorized": {"row_id": f"uncat:{p.key}", "net_amount": uncat[0],
                          "spending": f"{Decimal('0.00') - Decimal(uncat[0]):.2f}", "count": uncat[1]},
        "coverage": {"row_id": "coverage", "first_date": coverage[0], "last_date": coverage[1],
                     "period_count": coverage[2]},
    }
    if selection:
        picked = sorted(selection)
        sel = sum((Decimal(r["net_amount"]) for r in rows if r["category"] in picked), Decimal("0.00"))
        out["selection"] = {"row_id": f"sel:{'+'.join(picked)}:{p.key}", "categories": picked,
                            "net_amount": f"{sel:.2f}", "spending": f"{Decimal('0.00') - sel:.2f}",
                            "count": sum(r["count"] for r in rows if r["category"] in picked)}
    return out


def count_result(period, today, categories: list[str], money_out: int, total: int | None = None, *,
                 coverage=("2026-09-01", "2026-10-17", 30)) -> dict:
    """A get_transactions-shaped result; rows are left empty, since the count template never reads them."""
    p = resolve(period, today)
    return {
        "period": period_json(p),
        "count_row": {"row_id": f"txncount:{p.key}:{'+'.join(sorted(categories))}",
                      "total_count": money_out if total is None else total, "money_out_count": money_out},
        "rows": [],
        "truncated": False,
        "coverage": {"row_id": "coverage", "first_date": coverage[0], "last_date": coverage[1],
                     "period_count": coverage[2]},
    }


def fp(period, categories, phrase="", intent="category_total") -> FinalPlan:
    """A category_total plan (or transaction_count with intent=)."""
    return FinalPlan(intent=intent, period=period, categories=categories, category_phrase=phrase)


def cp(period, categories, phrase="") -> FinalPlan:
    """A transaction_count plan."""
    return fp(period, categories, phrase, intent="transaction_count")


SEP_RANGE = ExplicitRange(start=date(2026, 9, 1), end=date(2026, 9, 30), explicit_reason="the month of September")
XMAS_RANGE = ExplicitRange(start=date(2025, 12, 15), end=date(2026, 1, 14), explicit_reason="winter break")

CASES: list[Case] = [
    Case("single_dining_sep", "How much did I spend on dining in September?",
         fp("2026-09", ["Dining"]), result("2026-09", TODAY, SEP), TODAY,
         "You spent $61.87 on Dining in September.\n"
         "(Showing Dining for Sep 1–30, 2026.)"),
    Case("multi_food_oct", "What did I spend on food this month?",
         fp("this_month", ["Dining", "Groceries"], "food"),
         result("this_month", TODAY, {"Dining": ("-57.82", 6), "Groceries": ("-58.94", 3)},
                selection=["Dining", "Groceries"]), TODAY,
         "You spent $57.82 on Dining so far this month.\n"
         "You spent $58.94 on Groceries so far this month.\n"
         "Together: $116.76.\n"
         "(Showing “food” as Dining and Groceries for Oct 1–19, 2026.)\n"
         "Your imported transactions only go through Oct 17, 2026, so this may be incomplete."),
    Case("all_spending_sep", "How much did I spend in September?",
         fp("2026-09", "all_spending"), result("2026-09", TODAY, SEP), TODAY,
         "You spent $1,851.45 on all spending categories in September.\n"
         "(Showing all spending categories for Sep 1–30, 2026.)"),
    Case("refund_single", "How much did I spend on shopping in September?",
         fp("2026-09", ["Shopping"]), result("2026-09", TODAY, {"Shopping": ("20.00", 2)}), TODAY,
         "Refunds in Shopping exceeded spending by $20.00 in September.\n"
         "(Showing Shopping for Sep 1–30, 2026.)"),
    Case("refund_selection", "How much did I spend on dining and shopping in September?",
         fp("2026-09", ["Dining", "Shopping"]),
         result("2026-09", TODAY, {"Dining": ("-10.00", 1), "Shopping": ("30.00", 1)},
                selection=["Dining", "Shopping"]), TODAY,
         "You spent $10.00 on Dining in September.\n"
         "Refunds in Shopping exceeded spending by $30.00 in September.\n"
         "Together, refunds exceeded spending by $20.00.\n"
         "(Showing Dining and Shopping for Sep 1–30, 2026.)"),
    Case("zero_education_sep", "How much did I spend on education in September?",
         fp("2026-09", ["Education"]), result("2026-09", TODAY, SEP), TODAY,
         "You spent $0.00 on Education in September.\n"
         "(Showing Education for Sep 1–30, 2026.)"),
    Case("no_data_dec", "How much did I spend on dining in December?",
         fp("2025-12", ["Dining"]), result("2025-12", TODAY, {}, coverage=("2026-09-01", "2026-10-17", 0)), TODAY,
         "I don't have any imported transactions for Dec 1–31, 2025, so I can't tell what you spent then."),
    Case("uncat_one", "How much did I spend on dining in September?",
         fp("2026-09", ["Dining"]), result("2026-09", TODAY, SEP, uncat=("-12.50", 1)), TODAY,
         "You spent $61.87 on Dining in September.\n"
         "(Showing Dining for Sep 1–30, 2026.)\n"
         "1 transaction ($12.50) isn't categorized yet and isn't included above."),
    Case("uncat_many", "How much did I spend in September?",
         fp("2026-09", "all_spending"), result("2026-09", TODAY, {}, uncat=("-1851.45", 31)), TODAY,
         "You spent $0.00 on all spending categories in September.\n"
         "(Showing all spending categories for Sep 1–30, 2026.)\n"
         "31 transactions ($1,851.45) aren't categorized yet and aren't included above."),
    Case("coverage_first_date", "How much did I spend on dining in September?",
         fp("2026-09", ["Dining"]), result("2026-09", TODAY, SEP, coverage=("2026-09-10", "2026-10-17", 20)), TODAY,
         "You spent $61.87 on Dining in September.\n"
         "(Showing Dining for Sep 1–30, 2026.)\n"
         "Your imported transactions start on Sep 10, 2026, so this may be incomplete."),
    Case("single_day_oct1", "How much did I spend on dining this month?",
         fp("this_month", ["Dining"]),
         result("this_month", date(2026, 10, 1), {"Dining": ("-4.50", 1)}, coverage=("2026-09-01", "2026-10-01", 1)),
         date(2026, 10, 1),
         "You spent $4.50 on Dining so far this month.\n"
         "(Showing Dining for Oct 1, 2026.)"),
    Case("cross_year", "How much did I spend on groceries over winter break?",
         fp(XMAS_RANGE, ["Groceries"]),
         result(XMAS_RANGE, TODAY, {"Groceries": ("-45.00", 2)}, coverage=("2025-12-01", "2026-10-17", 9)), TODAY,
         "You spent $45.00 on Groceries in the selected dates.\n"
         "(Showing Groceries for Dec 15, 2025 – Jan 14, 2026.)"),
    Case("explicit_full_month", "How much did I spend on dining from Sep 1 to Sep 30?",
         fp(SEP_RANGE, ["Dining"]), result(SEP_RANGE, TODAY, SEP), TODAY,
         "You spent $61.87 on Dining in September.\n"
         "(Showing Dining for Sep 1–30, 2026.)"),
    Case("three_categories", "How much did I spend on getting around and bills in September?",
         fp("2026-09", ["Transport", "Utilities", "Subscriptions"], "bills"),
         result("2026-09", TODAY, SEP, selection=["Transport", "Utilities", "Subscriptions"]), TODAY,
         "You spent $89.36 on Transport in September.\n"
         "You spent $77.74 on Utilities in September.\n"
         "You spent $24.46 on Subscriptions in September.\n"
         "Together: $191.56.\n"
         "(Showing “bills” as Transport, Utilities and Subscriptions for Sep 1–30, 2026.)"),
    Case("emoji_phrase", "How much did I spend on 🍕 food in September?",
         fp("2026-09", ["Dining", "Groceries"], "🍕 food"),
         result("2026-09", TODAY, SEP, selection=["Dining", "Groceries"]), TODAY,
         "You spent $61.87 on Dining in September.\n"
         "You spent $161.39 on Groceries in September.\n"
         "Together: $223.26.\n"
         "(Showing “🍕 food” as Dining and Groceries for Sep 1–30, 2026.)"),
    Case("count_eat_out_oct", "How many times did I eat out in October?",
         cp("2026-10", ["Dining"], "eat out"),
         count_result("2026-10", NOV5, ["Dining"], 9, 10, coverage=("2026-09-01", "2026-10-30", 40)), NOV5,
         "You made 9 purchases in October.\n"
         "(Showing “eat out” as Dining for Oct 1–31, 2026.)\n"
         "Your imported transactions only go through Oct 30, 2026, so this may be incomplete."),
    Case("count_one_shopping", "How many shopping purchases did I make in September?",
         cp("2026-09", ["Shopping"]), count_result("2026-09", TODAY, ["Shopping"], 1), TODAY,
         "You made 1 purchase in September.\n"
         "(Showing Shopping for Sep 1–30, 2026.)"),
    Case("count_all_spending_sep", "How many purchases did I make in September?",
         cp("2026-09", "all_spending"), count_result("2026-09", TODAY, list(SPENDING_CATEGORIES), 30), TODAY,
         "You made 30 purchases in September.\n"
         "(Showing all spending categories for Sep 1–30, 2026.)"),
    Case("count_no_data_dec", "How many times did I eat out in December?",
         cp("2025-12", ["Dining"], "eat out"),
         count_result("2025-12", TODAY, ["Dining"], 0, coverage=("2026-09-01", "2026-10-17", 0)), TODAY,
         "I don't have any imported transactions for Dec 1–31, 2025, so I can't tell what you spent then."),
]


def recurring_result(rows=(), stopped=0, last="2026-10-30", count=63) -> dict:
    """A find_recurring-shaped result from (merchant, amount, next expected) tuples, largest first."""
    out = [{"row_id": f"series:{m}:key", "key": f"{m}:key", "merchant": m, "amount": a, "next_expected": n}
           for m, a, n in rows]
    total = sum((Decimal(r["amount"]) for r in out), Decimal("0.00"))
    return {"rows": out, "total": {"row_id": "rectotal", "amount": f"{total:.2f}", "count": len(out)},
            "stopped": {"row_id": "stopped", "count": stopped},
            "coverage": {"row_id": "coverage", "last_date": last if count else None, "count": count}}


def flag_result(rows=(), last="2026-10-30", count=63) -> dict:
    """A detect_anomalies-shaped result from (flag id, kind, merchant, amount, date) tuples, newest first."""
    out = [{"row_id": f"flag:{i}", "kind": k, "merchant": m, "amount": a, "date": d} for i, k, m, a, d in rows]
    return {"rows": out, "total": {"row_id": "flagcount", "count": len(out)},
            "coverage": {"row_id": "coverage", "last_date": last if count else None, "count": count}}


FIXTURE_SERIES = [
    ("LANDMARK COLLEGE PARK RENT", "-925.00", "2026-10-31"), ("COMCAST XFINITY", "-32.50", "2026-11-09"),
    ("MINT MOBILE", "-15.00", "2026-11-13"), ("PLANET FITNESS CLUB", "-10.00", "2026-11-15"),
    ("NETFLIX", "-7.99", "2026-11-18"), ("PRIME VIDEO CHANNELS", "-7.49", "2026-11-14"),
    ("SPOTIFY USA", "-5.99", "2026-11-16"), ("APPLE", "-2.99", "2026-11-03"),
]
FIXTURE_FLAGS = [
    (3, "unfamiliar_merchant", "XJ4HOLDINGS", "-225.56", "2026-10-24"), (1, "duplicate", "UBER", "-25.14", "2026-09-08"),
    (2, "large_charge", "AMAZON", "-403.43", "2026-09-06"),
]
RECURRING, FLAGS = ListPlan(intent="recurring_list"), ListPlan(intent="flag_list")

CASES += [
    Case("recurring_fixture", "What subscriptions do I have?", RECURRING, recurring_result(FIXTURE_SERIES), NOV5,
         "You have 8 recurring payments, $1,006.96 a month in total:\n"
         "- LANDMARK COLLEGE PARK RENT: $925.00 a month, next expected Oct 31, 2026\n"
         "- COMCAST XFINITY: $32.50 a month, next expected Nov 9, 2026\n"
         "- MINT MOBILE: $15.00 a month, next expected Nov 13, 2026\n"
         "- PLANET FITNESS CLUB: $10.00 a month, next expected Nov 15, 2026\n"
         "- NETFLIX: $7.99 a month, next expected Nov 18, 2026\n"
         "- PRIME VIDEO CHANNELS: $7.49 a month, next expected Nov 14, 2026\n"
         "- SPOTIFY USA: $5.99 a month, next expected Nov 16, 2026\n"
         "- APPLE: $2.99 a month, next expected Nov 3, 2026\n"
         "Based on transactions through Oct 30, 2026."),
    Case("recurring_one_and_one_stopped", "What are my recurring payments?", RECURRING,
         recurring_result([("7 ELEVEN 1234", "-1250.00", "2026-11-20")], stopped=1), NOV5,
         "You have 1 recurring payment, $1,250.00 a month in total:\n"
         "- 7 ELEVEN 1234: $1,250.00 a month, next expected Nov 20, 2026\n"
         "1 payment has stopped. See the Recurring page for details.\n"
         "Based on transactions through Oct 30, 2026."),
    Case("recurring_only_stopped", "What subscriptions do I have?", RECURRING, recurring_result(stopped=2), NOV5,
         "I haven't found any active recurring payments in your transactions.\n"
         "2 payments have stopped. See the Recurring page for details.\n"
         "Based on transactions through Oct 30, 2026."),
    Case("recurring_none", "Do I have any subscriptions?", RECURRING, recurring_result(), NOV5,
         "I haven't found any active recurring payments in your transactions.\n"
         "Based on transactions through Oct 30, 2026."),
    Case("recurring_no_data", "What subscriptions do I have?", RECURRING, recurring_result(count=0), NOV5,
         "You don't have any imported transactions yet, so I can't look for recurring payments."),
    Case("flags_fixture", "Do I have any duplicate or unusual charges?", FLAGS, flag_result(FIXTURE_FLAGS), NOV5,
         "You have 3 open flags:\n"
         "- New merchant: XJ4HOLDINGS, $225.56 on Oct 24, 2026\n"
         "- Possible duplicate: UBER, $25.14 on Sep 8, 2026\n"
         "- Unusually large: AMAZON, $403.43 on Sep 6, 2026\n"
         "Review them on the Flags page."),
    Case("flags_one", "Anything weird on my account?", FLAGS, flag_result(FIXTURE_FLAGS[1:2]), NOV5,
         "You have 1 open flag:\n"
         "- Possible duplicate: UBER, $25.14 on Sep 8, 2026\n"
         "Review them on the Flags page."),
    Case("flags_none", "Any duplicate charges?", FLAGS, flag_result(), NOV5, "You have no open flags."),
    Case("flags_no_data", "Any duplicate charges?", FLAGS, flag_result(count=0), NOV5,
         "You don't have any imported transactions yet, so there's nothing to flag."),
]
BY_NAME = {c.name: c for c in CASES}


def render(case: Case) -> tuple[str, list[dict], dict]:
    """(text, slot dicts, stored plan) for a case, as the loop would store them."""
    if isinstance(case.plan, ListPlan):
        renderer = render_recurring_list if case.plan.intent == "recurring_list" else render_flag_list
        text, slots = renderer(case.result)
        return text, [asdict(s) for s in slots], list_plan_json(case.plan, case.result, case.today)
    renderer = render_category_total if case.plan.intent == "category_total" else render_transaction_count
    text, slots = renderer(case.plan, case.result)
    return text, [asdict(s) for s in slots], plan_json(case.plan, resolve(case.plan.period, case.today), case.today)
