"""Final check of every number in an answer, against the stored tool results.

Deliberately shares no formatting or parsing code with templates.py or periods.py, and imports only the
standard library, so a rendering bug or a post-render edit has to get past an independent implementation.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MONEY_RE = re.compile(r"^\$(?:0|[1-9]\d{0,2}(?:,\d{3})*)\.\d{2}$")  # commas required, no leading zeros
INT_RE = re.compile(r"^(?:0|[1-9]\d*)$")
MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
RANGE_RE = re.compile(
    r"^(?P<m1>[A-Z][a-z]{2}) (?P<d1>\d{1,2})(?:, (?P<y1>\d{4}))?"
    r"(?:–| – )(?:(?P<m2>[A-Z][a-z]{2}) )?(?P<d2>\d{1,2}), (?P<y2>\d{4})$"
)
SINGLE_DATE_RE = re.compile(r"^(?P<m>[A-Z][a-z]{2}) (?P<d>\d{1,2}), (?P<y>\d{4})$")  # "Oct 1, 2026"

# Step 6: every line of the template text, with each slot replaced by {kind}, must match one of these.
# Written here independently of templates.py, so an edited or wrong literal ("You saved …") fails.
_CATS = r"(?:all spending categories|\{category\}(?:(?:, \{category\})* and \{category\})?)"
SKELETONS = {
    "category_total": [
        r"You spent \{money_spending\} on (?:\{category\}|all spending categories) \{period_label\}\.",
        r"Refunds in (?:\{category\}|all spending categories) exceeded spending by \{money_refund\} \{period_label\}\.",
        r"Together: \{money_spending\}\.",
        r"Together, refunds exceeded spending by \{money_refund\}\.",
        r"\(Showing (?:“\{phrase\}” as )?" + _CATS + r" for \{date_range\}\.\)",
        r"Your imported transactions only go through \{date\}, so this may be incomplete\.",
        r"Your imported transactions start on \{date\}, so this may be incomplete\.",
        r"\{count\} transaction \(\{money_spending\}\) isn't categorized yet and isn't included above\.",
        r"\{count\} transactions \(\{money_spending\}\) aren't categorized yet and aren't included above\.",
        r"I don't have any imported transactions for \{date_range\}, so I can't tell what you spent then\.",
    ],
    "transaction_count": [
        r"You made \{count\} purchases? \{period_label\}\.",
        r"\(Showing (?:“\{phrase\}” as )?" + _CATS + r" for \{date_range\}\.\)",
        r"Your imported transactions only go through \{date\}, so this may be incomplete\.",
        r"Your imported transactions start on \{date\}, so this may be incomplete\.",
        r"I don't have any imported transactions for \{date_range\}, so I can't tell what you spent then\.",
    ],
}
# list_plan answers (what the user has now): no period, so their own skeletons and completeness rules.
SKELETONS["recurring_list"] = [
    r"You don't have any imported transactions yet, so I can't look for recurring payments\.",
    r"I haven't found any active recurring payments in your transactions\.",
    r"You have \{count\} recurring payments?, \{money_charge\} a month in total:",
    r"- \{merchant\}: \{money_charge\} a month, next expected \{date\}",
    r"\{count\} payments? (?:has|have) stopped\. See the Recurring page for details\.",
    r"Based on transactions through \{date\}\.",
]
SKELETONS["flag_list"] = [
    r"You don't have any imported transactions yet, so there's nothing to flag\.",
    r"You have no open flags\.",
    r"You have \{count\} open flags?:",
    r"- \{flag_label\}: \{merchant\}, \{money_charge\} on \{date\}",
    r"Review them on the Flags page\.",
]
LIST_INTENTS = {"recurring_list", "flag_list"}
# The verifier's own copy of the flag labels; templates.py has the other one.
FLAG_LABEL = {"duplicate": "Possible duplicate", "large_charge": "Unusually large",
              "unfamiliar_merchant": "New merchant"}
MERCHANT_MAX = 100
MERCHANT_FORBIDDEN = set("\n\r$“”")  # can't pass as part of the sentence around it

FULL_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


@dataclass
class Verdict:
    ok: bool
    reason: str = ""


def index_results(final_results: list[dict]) -> dict[str, tuple[dict, dict]]:
    """row_id -> (row, period) over the stored final-run tool results."""
    idx = {}
    for res in final_results:
        for row in [*res.get("rows", []), res.get("total"), res.get("uncategorized"), res.get("count_row"),
                    res.get("selection"), res.get("coverage"), res.get("stopped")]:
            if row:
                idx[row["row_id"]] = (row, res.get("period"))
    return idx


def verify(text: str, extra: str | None, slots: list[dict], final_results: list[dict],
           question: str, plan: dict) -> Verdict:
    """Pass only if every digit sits in a slot, every slot matches its source, and the answer is complete."""
    full = text if extra is None else text + "\n" + extra
    idx = index_results(final_results)

    # Step 0: slot geometry — inside the template text, sorted, non-overlapping, unique.
    spans = sorted((s["start"], s["end"]) for s in slots)
    if any(a < 0 or a >= b for a, b in spans) or any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1)):
        return Verdict(False, "overlapping or empty slots")
    if spans and spans[-1][1] > len(text):
        return Verdict(False, "slot outside template text")

    # Step 1+2: every digit (any script) must sit inside a slot span.
    for i, ch in enumerate(full):
        if unicodedata.category(ch) == "Nd" or ch.isdigit():
            if not any(a <= i < b for a, b in spans):
                return Verdict(False, f"number outside any slot at {i}")

    if plan.get("intent") in LIST_INTENTS:
        return _verify_list(text, extra, slots, final_results, idx, plan["intent"])

    # Step 3+4: each slot's displayed value matches its source.
    plan_start, plan_end = date.fromisoformat(plan["resolved"]["start"]), date.fromisoformat(plan["resolved"]["end"])
    for s in slots:
        shown = text[s["start"]:s["end"]]
        if s["row_id"] in ("plan", "question"):
            row, period = None, None
        elif s["row_id"] in idx:
            row, period = idx[s["row_id"]]
        else:
            return Verdict(False, f"unknown row_id {s['row_id']}")
        try:
            ok = _check_slot(s["kind"], s["field"], shown, row, plan, plan_start, plan_end, question)
        except (KeyError, TypeError, ValueError, IndexError):
            ok = False  # a slot pointing at a field its row doesn't have
        if not ok:
            return Verdict(False, f"slot {s['kind']} {s['row_id']}.{s['field']} shows {shown!r}")
        if period and (period["start"], period["end"]) != (plan["resolved"]["start"], plan["resolved"]["end"]):
            return Verdict(False, f"{s['row_id']} is from a different period than the plan")

    # Step 5: completeness, per intent — every required part is present, nothing extra.
    reason = _check_complete(slots, idx, plan)
    if reason:
        return Verdict(False, reason)

    # Step 6: the words around the slots are an allowed sentence.
    if not _check_skeleton(text, slots, plan["intent"]):
        return Verdict(False, "template text doesn't match an allowed sentence")
    return Verdict(True)


def _check_complete(slots: list[dict], idx: dict, plan: dict) -> str | None:
    """A reason if the answer leaves out or adds a required part; None if complete."""
    res = plan["resolved"]
    key = res["key"]
    if "coverage" not in idx:
        return "coverage row missing"
    cov = idx["coverage"][0]
    if cov["period_count"] == 0:
        has_numbers = any(s["kind"] == "count" or s["kind"].startswith("money") for s in slots)
        return "no-data answer contains numbers" if has_numbers else None
    need_dates = sorted(f for f, cond in (("last_date", cov["last_date"] < res["end"]),
                                          ("first_date", cov["first_date"] > res["start"])) if cond)
    if sorted(s["field"] for s in slots if s["kind"] == "date") != need_dates:
        return "coverage note missing or extra"
    cats = plan["categories"]
    shown = [s for s in slots if s["row_id"] == "plan" and s["kind"] == "category"]
    if (0 if cats == "all_spending" else len(cats)) != len(shown):
        return "interpretation line doesn't list exactly the planned categories"

    if plan["intent"] == "transaction_count":
        return _check_count_complete(slots, key, cats)

    want =[f"total:{key}"] if cats == "all_spending" else [f"cat:{c}:{key}" for c in cats]
    if isinstance(cats, list) and len(cats) > 1:
        want.append(f"sel:{'+'.join(sorted(cats))}:{key}")
    unc = idx.get(f"uncat:{key}")
    if unc and unc[0]["count"] > 0:
        want += [f"uncat:{key}"]  # the uncategorized note is required, not optional
    got = [s["row_id"] for s in slots if s["kind"].startswith("money")]
    if sorted(got) != sorted(want):
        return "answer does not cover exactly the planned rows"
    counts = [s["row_id"] for s in slots if s["kind"] == "count"]
    if counts != ([f"uncat:{key}"] if unc and unc[0]["count"] > 0 else []):
        return "answer does not cover exactly the planned rows"
    return None


def _check_count_complete(slots: list[dict], key: str, cats) -> str | None:
    """Exactly one purchase count, from the count row for the planned categories, and no amounts."""
    if any(s["kind"].startswith("money") for s in slots):
        return "count answer contains an amount"
    counts = [s for s in slots if s["kind"] == "count"]
    if len(counts) != 1 or counts[0]["field"] != "money_out_count":
        return "count answer must state exactly one purchase count"
    prefix = f"txncount:{key}:"
    row_id = counts[0]["row_id"]
    if not row_id.startswith(prefix):
        return "count row is not for the planned period"
    row_cats = row_id[len(prefix):]
    # all_spending is the 11 spending categories; "all" would count payroll and transfers as purchases
    if (row_cats == "all") if cats == "all_spending" else (row_cats != "+".join(sorted(cats))):
        return "count row is not for the planned categories"
    return None


def _check_skeleton(text: str, slots: list[dict], intent: str) -> bool:
    """Replace each slot with {kind}; every resulting line must fully match an allowed sentence."""
    if intent not in SKELETONS:
        return False
    parts, pos = [], 0
    for s in sorted(slots, key=lambda s: s["start"]):
        parts += [text[pos:s["start"]], "{" + s["kind"] + "}"]
        pos = s["end"]
    parts.append(text[pos:])
    return all(any(re.fullmatch(p, line) for p in SKELETONS[intent]) for line in "".join(parts).split("\n"))


def _check_slot(kind, field, shown, row, plan, start, end, question) -> bool:
    """The independent rule for one slot kind."""
    if kind in ("money_spending", "money_refund"):
        if not MONEY_RE.match(shown):
            return False
        value = Decimal(shown[1:].replace(",", ""))
        net = Decimal(row["net_amount"])
        return value == (-net if kind == "money_spending" else net) and value >= 0 \
            and (kind == "money_spending") == (net <= 0)
    if kind == "count":
        return bool(INT_RE.match(shown)) and int(shown) == int(row[field])
    if kind == "category":
        if row is None:  # interpretation line: field is "categories.<i>"
            cats, i = plan["categories"], int(field.split(".")[1])
            return isinstance(cats, list) and i < len(cats) and shown == cats[i]
        return shown == row["category"]
    if kind == "date_range":
        return _parse_range(shown) == (start, end)
    if kind == "date":  # coverage note dates
        return row[field] is not None and _parse_single(shown) == date.fromisoformat(row[field])
    if kind == "period_label":
        return shown == _expected_label(start, end, plan["as_of"])
    if kind == "phrase":
        return (len(shown) >= 3 and not any(ch.isdigit() for ch in shown)
                and re.search(rf"(?<!\w){re.escape(shown)}(?!\w)", question, re.I) is not None)
    return False


def _mk(y: str, mon: str, d: str) -> date | None:
    """A date from text parts; None for an unknown month, a zero-padded day or an impossible date."""
    if mon not in MON or d.startswith("0"):
        return None
    try:
        return date(int(y), MON[mon], int(d))
    except ValueError:
        return None


def _parse_single(s: str) -> date | None:
    """'Oct 1, 2026' -> date, else None."""
    m = SINGLE_DATE_RE.match(s)
    return _mk(m["y"], m["m"], m["d"]) if m else None


def _parse_range(s: str) -> tuple[date, date] | None:
    """'Oct 1, 2026' (start = end), 'Sep 1–30, 2026', 'Sep 1 – Oct 19, 2026', 'Dec 15, 2025 – Jan 14, 2026'."""
    one = _parse_single(s)
    if one:
        return one, one
    m = RANGE_RE.match(s)
    if not m:
        return None
    start = _mk(m["y1"] or m["y2"], m["m1"], m["d1"])
    end = _mk(m["y2"], m["m2"] or m["m1"], m["d2"])
    if start is None or end is None or start >= end:
        return None
    return start, end


def _expected_label(start: date, end: date, as_of: str) -> str:
    """The period-label rule, re-derived from the dates alone."""
    today = date.fromisoformat(as_of)
    if start == today.replace(day=1) and end == today:
        return "so far this month"
    next_month = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    if start.day == 1 and (next_month - end).days == 1:
        return f"in {FULL_MONTHS[start.month - 1]}"
    return "in the selected dates"


def _verify_list(text: str, extra: str | None, slots: list[dict], final_results: list[dict], idx: dict,
                 intent: str) -> Verdict:
    """Steps 3-6 for a recurring or flag list: each slot matches its row, the answer is exactly the result's rows
    in order (with the counts and total this function re-adds itself), and every line is an allowed sentence."""
    if extra is not None:
        return Verdict(False, "list answers have no extra sentence")
    if len(final_results) != 1:
        return Verdict(False, "a list answer comes from exactly one result")
    res = final_results[0]
    for s in slots:
        shown = text[s["start"]:s["end"]]
        if s["row_id"] not in idx:
            return Verdict(False, f"unknown row_id {s['row_id']}")
        try:
            ok = _check_list_slot(s["kind"], s["field"], shown, idx[s["row_id"]][0])
        except (KeyError, TypeError, ValueError, ArithmeticError):
            ok = False
        if not ok:
            return Verdict(False, f"slot {s['kind']} {s['row_id']}.{s['field']} shows {shown!r}")
    try:
        reason = _check_list_complete(slots, res, intent)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        reason = "result is not a list result"
    if reason:
        return Verdict(False, reason)
    if not _check_skeleton(text, slots, intent):
        return Verdict(False, "template text doesn't match an allowed sentence")
    # The one line with no value in it, so dropping it wouldn't move any slot.
    if intent == "flag_list" and res["rows"] and text.split("\n")[-1] != "Review them on the Flags page.":
        return Verdict(False, "flag list doesn't end with the Flags page line")
    return Verdict(True)


def _check_list_slot(kind: str, field: str, shown: str, row: dict) -> bool:
    """The independent rule for one slot kind in a list answer."""
    if kind == "merchant":
        return field == "merchant" and shown == row["merchant"] and 0 < len(shown) <= MERCHANT_MAX \
            and not (set(shown) & MERCHANT_FORBIDDEN)
    if kind == "money_charge":
        if not MONEY_RE.match(shown):
            return False
        amount = Decimal(row[field])
        return amount <= 0 and Decimal(shown[1:].replace(",", "")) == -amount
    if kind == "count":
        return bool(INT_RE.match(shown)) and int(shown) == int(row[field])
    if kind == "date":
        return row[field] is not None and _parse_single(shown) == date.fromisoformat(row[field])
    if kind == "flag_label":
        return field == "kind" and shown == FLAG_LABEL.get(row["kind"])
    return False


def _check_list_complete(slots: list[dict], res: dict, intent: str) -> str | None:
    """A reason unless the slots are exactly, in order, what the result says the answer must show."""
    got = [(s["row_id"], s["kind"], s["field"]) for s in sorted(slots, key=lambda s: s["start"])]
    rows, total, cov = res["rows"], res["total"], res["coverage"]
    if cov["count"] == 0:
        return None if not got else "no-data answer contains values"
    if len({r["row_id"] for r in rows}) != len(rows):
        return "result rows repeat"
    if total["count"] != len(rows):
        return "count doesn't match the rows"
    want: list[tuple[str, str, str]] = []
    if intent == "recurring_list":
        added = sum((Decimal(r["amount"]) for r in rows), Decimal("0.00"))
        if Decimal(total["amount"]) != added:
            return "monthly total doesn't add up"
        if rows:
            want += [(total["row_id"], "count", "count"), (total["row_id"], "money_charge", "amount")]
        for r in rows:
            want += [(r["row_id"], "merchant", "merchant"), (r["row_id"], "money_charge", "amount"),
                     (r["row_id"], "date", "next_expected")]
        if res["stopped"]["count"] > 0:
            want.append((res["stopped"]["row_id"], "count", "count"))
        want.append((cov["row_id"], "date", "last_date"))
    else:
        if rows:
            want.append((total["row_id"], "count", "count"))
        for r in rows:
            want += [(r["row_id"], "flag_label", "kind"), (r["row_id"], "merchant", "merchant"),
                     (r["row_id"], "money_charge", "amount"), (r["row_id"], "date", "date")]
    return None if got == want else "answer does not show exactly the result's rows, in order"
