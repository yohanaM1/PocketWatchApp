"""Fixed answer text with slots: every number is rendered by code and tied to a (row_id, field, kind)."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Slot:
    start: int  # Python str index = Unicode code point, not UTF-16 unit (the UI indexes the same way)
    end: int
    row_id: str  # or "plan" / "question" / "coverage" for non-amount slots
    field: str
    kind: str  # money_spending | money_refund | count | date_range | date | category | period_label | phrase


class _Builder:
    """Appends literals and slots, tracking code-point offsets."""

    def __init__(self):
        """Start with empty text."""
        self.parts: list[str] = []
        self.slots: list[Slot] = []
        self.pos = 0

    def lit(self, s: str) -> "_Builder":
        """Append fixed template text; it may never contain a digit."""
        assert not any(ch.isdigit() for ch in s), f"template literal contains a digit: {s!r}"
        self.parts.append(s)
        self.pos += len(s)
        return self

    def slot(self, s: str, row_id: str, field: str, kind: str) -> "_Builder":
        """Append a value and record where it sits and where it came from."""
        self.slots.append(Slot(self.pos, self.pos + len(s), row_id, field, kind))
        self.parts.append(s)
        self.pos += len(s)
        return self

    def build(self) -> tuple[str, list[Slot]]:
        """The text and its slots."""
        return "".join(self.parts), self.slots


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # not %b (locale-dependent)


def money(d: Decimal) -> str:
    """'$1,234.56'."""
    return f"${d:,.2f}"


def _neg(d: Decimal) -> Decimal:
    """-d without producing a negative zero (which would render as '$-0.00')."""
    return Decimal("0.00") - d


def date_range(start: date, end: date) -> str:
    """'Oct 1, 2026', 'Sep 1–30, 2026', 'Sep 1 – Oct 19, 2026' or 'Dec 15, 2025 – Jan 14, 2026'."""
    a, z = MONTH_ABBR[start.month - 1], MONTH_ABBR[end.month - 1]
    if start == end:
        return f"{a} {start.day}, {start.year}"
    if start.year != end.year:
        return f"{a} {start.day}, {start.year} – {z} {end.day}, {end.year}"
    if start.month != end.month:
        return f"{a} {start.day} – {z} {end.day}, {end.year}"
    return f"{a} {start.day}–{end.day}, {end.year}"


def _dates(period: dict) -> tuple[date, date]:
    """(start, end) of a tool result's period block."""
    return date.fromisoformat(period["start"]), date.fromisoformat(period["end"])


def _no_data(b: _Builder, period: dict) -> None:
    """The whole answer when the period has no imported transactions: no data is not $0.00 or zero purchases."""
    b.lit("I don't have any imported transactions for ")
    b.slot(date_range(*_dates(period)), "plan", "period", "date_range")
    b.lit(", so I can't tell what you spent then.")


def _interpretation(b: _Builder, plan, period: dict) -> None:
    """'(Showing “food” as Dining and Groceries for Sep 1–30, 2026.)' on its own line."""
    b.lit("\n(Showing ")
    if plan.category_phrase:
        b.lit("“")
        b.slot(plan.category_phrase, "question", "category_phrase", "phrase")
        b.lit("” as ")
    if plan.categories == "all_spending":
        b.lit("all spending categories")
    else:  # names are slots checked against the plan
        for i, c in enumerate(plan.categories):
            if i:
                b.lit(" and " if i == len(plan.categories) - 1 else ", ")
            b.slot(c, "plan", f"categories.{i}", "category")
    b.lit(" for ")
    b.slot(date_range(*_dates(period)), "plan", "period", "date_range")
    b.lit(".)")


def _coverage_notes(b: _Builder, cov: dict, period: dict) -> None:
    """A note when the imports stop before the period ends or start after it starts."""
    if cov["last_date"] < period["end"]:
        last = date.fromisoformat(cov["last_date"])
        b.lit("\nYour imported transactions only go through ")
        b.slot(date_range(last, last), "coverage", "last_date", "date")
        b.lit(", so this may be incomplete.")
    if cov["first_date"] > period["start"]:
        first = date.fromisoformat(cov["first_date"])
        b.lit("\nYour imported transactions start on ")
        b.slot(date_range(first, first), "coverage", "first_date", "date")
        b.lit(", so this may be incomplete.")


def render_category_total(plan, result) -> tuple[str, list[Slot]]:
    """Fixed sentence per category (or the total), the selection total, the interpretation line,
    the coverage note and the uncategorized note."""
    b, period, cov = _Builder(), result["period"], result["coverage"]
    if cov["period_count"] == 0:
        _no_data(b, period)
        return b.build()
    by_cat = {r["category"]: r for r in result["rows"]}
    targets = [result["total"]] if plan.categories == "all_spending" else [by_cat[c] for c in plan.categories]

    for i, row in enumerate(targets):
        if i:
            b.lit("\n")
        net = Decimal(row["net_amount"])

        def name_slot(row=row):
            """The category name (a slot) or the literal for the total row."""
            if row is result["total"]:
                b.lit("all spending categories")
            else:
                b.slot(row["category"], row["row_id"], "category", "category")

        if net > 0:
            b.lit("Refunds in ")
            name_slot()
            b.lit(" exceeded spending by ")
            b.slot(money(net), row["row_id"], "net_amount", "money_refund")
        else:
            b.lit("You spent ")
            b.slot(money(_neg(net)), row["row_id"], "net_amount", "money_spending")
            b.lit(" on ")
            name_slot()
        b.lit(" ")
        b.slot(period["label"], "plan", "period_label", "period_label")
        b.lit(".")

    sel = result.get("selection")  # only for 2+ categories
    if sel:
        net = Decimal(sel["net_amount"])
        if net > 0:
            b.lit("\nTogether, refunds exceeded spending by ")
            b.slot(money(net), sel["row_id"], "net_amount", "money_refund")
        else:
            b.lit("\nTogether: ")
            b.slot(money(_neg(net)), sel["row_id"], "net_amount", "money_spending")
        b.lit(".")

    _interpretation(b, plan, period)
    _coverage_notes(b, cov, period)

    unc = result["uncategorized"]
    if unc["count"] > 0:
        one = unc["count"] == 1
        b.lit("\n")
        b.slot(str(unc["count"]), unc["row_id"], "count", "count")
        b.lit(" transaction (" if one else " transactions (")
        b.slot(money(_neg(Decimal(unc["net_amount"]))), unc["row_id"], "net_amount", "money_spending")
        b.lit(") isn't categorized yet and isn't included above." if one
              else ") aren't categorized yet and aren't included above.")
    return b.build()


def render_transaction_count(plan, result) -> tuple[str, list[Slot]]:
    """'You made 9 purchases in October.', the interpretation line and the coverage note.
    Counts money-out rows only, so refunds and income are never "purchases"."""
    b, period, cov = _Builder(), result["period"], result["coverage"]
    if cov["period_count"] == 0:
        _no_data(b, period)
        return b.build()
    row = result["count_row"]
    n = row["money_out_count"]
    b.lit("You made ")
    b.slot(str(n), row["row_id"], "money_out_count", "count")
    b.lit(" purchase " if n == 1 else " purchases ")
    b.slot(period["label"], "plan", "period_label", "period_label")
    b.lit(".")
    _interpretation(b, plan, period)
    _coverage_notes(b, cov, period)
    return b.build()


# --- list_plan answers: what the user has now, so no period and no interpretation line -------------------------

FLAG_LABELS = {"duplicate": "Possible duplicate", "large_charge": "Unusually large",
               "unfamiliar_merchant": "New merchant"}


def _day(iso: str) -> str:
    """'Oct 21, 2026'."""
    d = date.fromisoformat(iso)
    return date_range(d, d)


def _charge(amount: str) -> str:
    """'$8.99' from a money-out amount ('-8.99')."""
    return money(_neg(Decimal(amount)))


def render_recurring_list(result) -> tuple[str, list[Slot]]:
    """Each active recurring payment with its amount and next expected date, the monthly total, how many
    have stopped, and the date the data runs through."""
    b, cov = _Builder(), result["coverage"]
    if cov["count"] == 0:
        b.lit("You don't have any imported transactions yet, so I can't look for recurring payments.")
        return b.build()
    rows, total, stopped = result["rows"], result["total"], result["stopped"]
    if not rows:
        b.lit("I haven't found any active recurring payments in your transactions.")
    else:
        b.lit("You have ")
        b.slot(str(total["count"]), total["row_id"], "count", "count")
        b.lit(" recurring payment, " if total["count"] == 1 else " recurring payments, ")
        b.slot(_charge(total["amount"]), total["row_id"], "amount", "money_charge")
        b.lit(" a month in total:")
        for r in rows:
            b.lit("\n- ")
            b.slot(r["merchant"], r["row_id"], "merchant", "merchant")
            b.lit(": ")
            b.slot(_charge(r["amount"]), r["row_id"], "amount", "money_charge")
            b.lit(" a month, next expected ")
            b.slot(_day(r["next_expected"]), r["row_id"], "next_expected", "date")
    if stopped["count"] > 0:
        b.lit("\n")
        b.slot(str(stopped["count"]), stopped["row_id"], "count", "count")
        b.lit(" payment has stopped." if stopped["count"] == 1 else " payments have stopped.")
        b.lit(" See the Recurring page for details.")
    b.lit("\nBased on transactions through ")
    b.slot(_day(cov["last_date"]), cov["row_id"], "last_date", "date")
    b.lit(".")
    return b.build()


def render_flag_list(result) -> tuple[str, list[Slot]]:
    """Each open flag with its label, merchant, amount and date, then a pointer to the Flags page."""
    b, cov = _Builder(), result["coverage"]
    if cov["count"] == 0:
        b.lit("You don't have any imported transactions yet, so there's nothing to flag.")
        return b.build()
    rows, total = result["rows"], result["total"]
    if not rows:
        b.lit("You have no open flags.")
        return b.build()
    b.lit("You have ")
    b.slot(str(total["count"]), total["row_id"], "count", "count")
    b.lit(" open flag:" if total["count"] == 1 else " open flags:")
    for r in rows:
        b.lit("\n- ")
        b.slot(FLAG_LABELS[r["kind"]], r["row_id"], "kind", "flag_label")
        b.lit(": ")
        b.slot(r["merchant"], r["row_id"], "merchant", "merchant")
        b.lit(", ")
        b.slot(_charge(r["amount"]), r["row_id"], "amount", "money_charge")
        b.lit(" on ")
        b.slot(_day(r["date"]), r["row_id"], "date", "date")
    b.lit("\nReview them on the Flags page.")
    return b.build()
