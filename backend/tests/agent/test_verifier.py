"""The final verifier: correct answers pass, every kind of tampering fails with a specific reason."""

import copy
import json
from pathlib import Path

import pytest

from app.agent.plan import LIST_INTENTS
from app.agent.verifier import verify
from tests.agent.cases import BY_NAME, CASES, render

TAMPERED = sorted((Path(__file__).parent / "tampered").glob("*.json"))


def check(name, text=None, extra=None, slots=None, result=None, plan=None, question=None):
    """verify() on a case, with any part replaced."""
    case = BY_NAME[name]
    t, s, p = render(case)
    return verify(t if text is None else text, extra, s if slots is None else slots,
                  [case.result if result is None else result], case.question if question is None else question,
                  p if plan is None else plan)


def replace_once(text: str, old: str, new: str) -> str:
    """Exact single-occurrence replacement; fails loudly so a stale edit can't pass by doing nothing."""
    assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times"
    return text.replace(old, new)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_correct_answer_passes(case):
    """Every known-good case verifies, with and without a clean extra sentence.

    List answers (recurring, flags) never get one: they contain merchant names, which would reach the LLM."""
    text, slots, plan = render(case)
    assert verify(text, None, slots, [case.result], case.question, plan).ok
    with_extra = verify(text, "Nice work keeping track.", slots, [case.result], case.question, plan)
    assert with_extra.ok is (plan["intent"] not in LIST_INTENTS)


@pytest.mark.parametrize("path", TAMPERED, ids=lambda p: p.stem)
def test_tampered_case_fails(path):
    """Each data-file tampering fails verify() with the expected reason."""
    spec = json.loads(path.read_text())
    case = BY_NAME[spec["base"]]
    text, slots, plan = render(case)
    res = copy.deepcopy(case.result)
    for e in spec["text_edits"]:
        text = replace_once(text, e["old"], e["new"])
    for e in spec["slot_edits"]:
        slots[e["index"]].update(e["set"])
    for e in spec["result_edits"]:
        node = res
        for k in e["path"][:-1]:
            node = node[k]
        assert e["path"][-1] in node or isinstance(node, list)
        node[e["path"][-1]] = e["value"]
    v = verify(text, spec["extra"], slots, [res], case.question, plan)
    assert not v.ok
    assert spec["expect_reason"] in v.reason, v.reason


def test_swapped_months_via_period():
    """A slot pointing at October's row in a September answer fails the period check."""
    oct_case = BY_NAME["multi_food_oct"]
    text, slots, plan = render(BY_NAME["single_dining_sep"])
    v = verify(text, None, slots, [BY_NAME["single_dining_sep"].result, oct_case.result],
               BY_NAME["single_dining_sep"].question, plan)
    assert v.ok
    sep_result = BY_NAME["single_dining_sep"].result
    moved = copy.deepcopy(sep_result)
    moved["period"] = oct_case.result["period"]
    assert "different period" in check("single_dining_sep", result=moved).reason


@pytest.mark.parametrize("old,new,reason", [
    ("$61.87", "-$61.87", "number outside any slot"),  # the shift pushes a digit out of the slot
    ("$61.87", "$61.90", "slot money_spending"),
    ("$61.87", "$061.8", "slot money_spending"),
    ("on Dining", "on Rent!", "slot category"),
    ("You spent", "You saved", "allowed sentence"),
    ("in September", "in Septembre", "slot period_label"),
    ("Sep 1–30, 2026", "Sep 1–29, 2026", "slot date_range"),
])
def test_text_edits_fail(old, new, reason):
    """Post-render edits (slot offsets not moved) are caught."""
    case = BY_NAME["single_dining_sep"]
    text = render(case)[0]
    edited = text.replace(old, new, 1)
    assert reason in check("single_dining_sep", text=edited).reason


def test_digit_or_non_ascii_digit_outside_slots():
    """Digits outside slots fail, in the text, the extra sentence, or in another script."""
    assert "outside any slot" in check("single_dining_sep", extra="That's 2 meals out.").reason
    assert "outside any slot" in check("single_dining_sep", extra="Roughly ٦١ dollars.").reason
    assert "outside any slot" in check("single_dining_sep", extra="Roughly ６１ dollars.").reason
    text = render(BY_NAME["single_dining_sep"])[0] + " Up 5."
    assert "outside any slot" in check("single_dining_sep", text=text).reason


def test_dropped_category_and_missing_together():
    """Dropping a planned category's line, or the Together total, fails completeness."""
    text, slots, _ = render(BY_NAME["multi_food_oct"])
    lines = text.split("\n")
    cut = len(lines[0]) + 1
    shifted = [dict(s, start=s["start"] - cut, end=s["end"] - cut) for s in slots if s["start"] >= cut]
    v = check("multi_food_oct", text="\n".join(lines[1:]), slots=shifted)
    assert not v.ok and "planned rows" in v.reason

    start = text.index("\nTogether")
    end = text.index("\n", start + 1)
    cut = end - start
    no_sel = [s if s["start"] < start else dict(s, start=s["start"] - cut, end=s["end"] - cut)
              for s in slots if not s["row_id"].startswith("sel:")]
    v = check("multi_food_oct", text=text[:start] + text[end:], slots=no_sel)
    assert "planned rows" in v.reason


def test_wrong_total_in_selection():
    """A selection total that doesn't match its row fails."""
    res = copy.deepcopy(BY_NAME["multi_food_oct"].result)
    res["selection"]["net_amount"] = "-116.77"
    assert "slot money_spending sel:" in check("multi_food_oct", result=res).reason


def test_interpretation_line_category_checked_against_plan():
    """The interpretation line must name exactly the planned categories."""
    case = BY_NAME["single_dining_sep"]
    text = render(case)[0]
    swapped = text.replace("(Showing Dining", "(Showing Health")
    assert "slot category plan.categories.0" in check("single_dining_sep", text=swapped).reason
    _, _, plan = render(case)
    plan = dict(plan, categories=["Dining", "Groceries"])
    assert not check("single_dining_sep", plan=plan).ok


def test_overlapping_and_unknown_slots():
    """Overlapping spans and unknown row ids fail."""
    _, slots, _ = render(BY_NAME["single_dining_sep"])
    over = slots + [dict(slots[0], end=slots[0]["end"] + 2)]
    assert "overlapping" in check("single_dining_sep", slots=over).reason
    bad = [dict(s, row_id="cat:Dining:2026-08") if s["kind"].startswith("money") else s for s in slots]
    assert "unknown row_id" in check("single_dining_sep", slots=bad).reason


def test_uncategorized_and_coverage_notes_required():
    """Dropping the uncategorized note or the coverage note fails completeness."""
    text, slots, _ = render(BY_NAME["uncat_one"])
    cut = text.rindex("\n")
    v = check("uncat_one", text=text[:cut], slots=[s for s in slots if s["start"] < cut])
    assert "planned rows" in v.reason

    text, slots, _ = render(BY_NAME["coverage_first_date"])
    cut = text.rindex("\n")
    v = check("coverage_first_date", text=text[:cut], slots=[s for s in slots if s["start"] < cut])
    assert "coverage note" in v.reason


def test_money_format_strict():
    """$1234.56 (no comma) fails; $1,234.56 passes."""
    text = render(BY_NAME["all_spending_sep"])[0]
    assert check("all_spending_sep").ok
    assert "slot money_spending" in check("all_spending_sep", text=text.replace("$1,851.45", "$1851.45 ")).reason


def test_no_data_answer_with_zero_spliced_in_fails():
    """A correct no-data answer passes; the same answer with $0.00 spliced in as a slot fails."""
    assert check("no_data_dec").ok
    case = BY_NAME["no_data_dec"]
    text, slots, _ = render(case)
    spliced = text + " $0.00"
    extra_slot = {"start": len(text) + 1, "end": len(spliced), "row_id": "total:2025-12",
                  "field": "net_amount", "kind": "money_spending"}
    v = check("no_data_dec", text=spliced, slots=slots + [extra_slot])
    assert "no-data answer contains numbers" in v.reason


def test_phrase_must_come_from_question():
    """A phrase slot whose text isn't whole words of the question fails."""
    assert "slot phrase" in check("multi_food_oct", question="What did I spend on groceries?").reason


def test_count_must_match_its_row():
    """A purchase count that differs from money_out_count fails, as does showing total_count instead."""
    res = copy.deepcopy(BY_NAME["count_eat_out_oct"].result)
    res["count_row"]["money_out_count"] = 8
    assert "slot count txncount:" in check("count_eat_out_oct", result=res).reason

    text, slots, _ = render(BY_NAME["count_eat_out_oct"])
    i = next(j for j, s in enumerate(slots) if s["kind"] == "count")
    slots[i] = dict(slots[i], field="total_count")
    v = check("count_eat_out_oct", text=replace_once(text, "made 9 ", "made 10 "),
              slots=[dict(s, end=s["end"] + 1) if j == i else
                     dict(s, start=s["start"] + 1, end=s["end"] + 1) if s["start"] > slots[i]["start"] else s
                     for j, s in enumerate(slots)])
    assert not v.ok and "exactly one purchase count" in v.reason


def test_count_all_spending_must_not_count_everything():
    """An all_spending count taken from the unfiltered row (payroll, transfers) fails."""
    case = BY_NAME["count_all_spending_sep"]
    res = copy.deepcopy(case.result)
    res["count_row"]["row_id"] = "txncount:2026-09:all"
    _, slots, _ = render(case)
    slots = [dict(s, row_id="txncount:2026-09:all") if s["kind"] == "count" else s for s in slots]
    v = check("count_all_spending_sep", slots=slots, result=res)
    assert not v.ok and "planned categories" in v.reason


def test_count_row_for_other_categories_fails():
    """A Dining plan answered from a Groceries count row fails."""
    case = BY_NAME["count_eat_out_oct"]
    res = copy.deepcopy(case.result)
    res["count_row"]["row_id"] = "txncount:2026-10:Groceries"
    _, slots, _ = render(case)
    slots = [dict(s, row_id="txncount:2026-10:Groceries") if s["kind"] == "count" else s for s in slots]
    v = check("count_eat_out_oct", slots=slots, result=res)
    assert not v.ok and "planned categories" in v.reason


def test_count_answer_with_amount_spliced_in_fails():
    """A count answer may not carry a money line, even one that matches a real row."""
    v = check("count_one_shopping",
              text=BY_NAME["count_one_shopping"].expected_text + "\nYou spent $1.00 on Shopping in September.")
    assert not v.ok
