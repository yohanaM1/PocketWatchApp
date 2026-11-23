"""Generated tampering of every known-good answer in cases.py, for the verifier catch rate (spec 5.3).

Each mutation is a post-render edit: the text changes but the slots keep their offsets, which is
what tampering after rendering looks like. "Caught" means the answer would not be shown as
verified: verify() fails, or for the extra sentence, the loop's extra_sentence_ok() drops it
(the loop applies both checks to every extra sentence).

    python -m scripts.eval_verifier     # prints the catch rate and every survivor
"""

import re
from dataclasses import dataclass

from app.agent.loop import extra_sentence_ok
from app.agent.spending import SPENDING_CATEGORIES
from app.agent.verifier import verify
from tests.agent.cases import CASES, Case, render

# Literal words swapped for a near-synonym that changes the meaning
WORD_SWAPS = [
    ("You spent", "You saved"), ("spent", "earned"), ("Refunds", "Charges"), ("exceeded", "fell short of"),
    ("Together", "Each"), ("aren't categorized", "are categorized"), ("isn't categorized", "is categorized"),
    ("aren't included", "are included"), ("isn't included", "is included"), ("only go through", "go beyond"),
    ("start on", "end on"), ("may be incomplete", "is complete"), ("purchases", "refunds"),
    ("purchase ", "refund "), ("I don't have any", "I have all"), ("can't tell", "can tell"),
    ("so far this month", "last month"), ("in September", "in October"), ("in October", "in September"),
    ("in the selected dates", "this year"),
    # recurring and flag lists
    ("a month", "a year"), ("next expected", "last charged"), ("has stopped", "has started"),
    ("have stopped", "have started"), ("in total", "each"), ("open flag", "closed flag"),
    ("Possible duplicate", "Confirmed duplicate"), ("New merchant", "Known merchant"),
    ("Unusually large", "Unusually small"), ("I haven't found", "I have found"), ("no open flags", "many open flags"),
]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EXTRAS = ["That is 3 more purchases than usual.", "You spent about twice as much as before.",
          "Nice, that's a dozen fewer coffees.", "Only ten percent of your budget!", "Spending is up 5% this month."]
MONEY = re.compile(r"\$[\d,]+\.\d{2}")


@dataclass
class Mutation:
    case: str
    kind: str
    description: str
    text: str
    extra: str | None


def caught(case: Case, m: Mutation) -> bool:
    """True if the system would not show this as a verified answer (or would drop the extra sentence)."""
    _, slots, plan = render(case)
    if m.extra is not None and not extra_sentence_ok(m.extra):
        return True
    return not verify(m.text, m.extra, slots, [case.result], case.question, plan).ok


def generate() -> list[Mutation]:
    """Every mutation of every case. Mutations that leave the text unchanged are never produced."""
    out: list[Mutation] = []
    for case in CASES:
        text, slots, _ = render(case)

        def add(kind: str, desc: str, new_text: str, extra: str | None = None):
            """Keep a mutation only if it actually changes something."""
            if new_text != text or extra is not None:
                out.append(Mutation(case.name, kind, desc, new_text, extra))

        # each digit changed to every other digit
        for i, ch in enumerate(text):
            if ch.isdigit():
                for d in "0123456789":
                    if d != ch:
                        add("digit", f"char {i}: {ch}->{d}", text[:i] + d + text[i + 1:])

        # each pair of different money amounts swapped
        amounts = [(mt.start(), mt.end(), mt.group()) for mt in MONEY.finditer(text)]
        for a in range(len(amounts)):
            for b in range(a + 1, len(amounts)):
                (s1, e1, v1), (s2, e2, v2) = amounts[a], amounts[b]
                if v1 != v2:
                    add("swap", f"{v1} <-> {v2}", text[:s1] + v2 + text[e1:s2] + v1 + text[e2:])

        # a sign added; a comma or the decimal point removed
        for s, e, v in amounts:
            add("sign", f"-{v}", text[:s] + "-" + text[s:])
            add("sign", f"+{v}", text[:s] + "+" + text[s:])
            if "," in v:
                add("format", f"comma removed from {v}", text[:s] + v.replace(",", "") + text[e:])
            add("format", f"point removed from {v}", text[:s] + v.replace(".", "") + text[e:])

        # literal words replaced with a near-synonym (first occurrence)
        for old, new in WORD_SWAPS:
            if old in text:
                add("word", f"{old!r} -> {new!r}", text.replace(old, new, 1))

        # category names swapped for another category; month abbreviations swapped
        for cat in SPENDING_CATEGORIES:
            for mt in re.finditer(rf"\b{cat}\b", text):
                other = next(c for c in SPENDING_CATEGORIES if c != cat and len(c) == len(cat)) \
                    if any(c != cat and len(c) == len(cat) for c in SPENDING_CATEGORIES) else "Other"
                add("category", f"{cat} -> {other} at {mt.start()}", text[:mt.start()] + other + text[mt.end():])
        for mt in re.finditer(r"\b(" + "|".join(MONTHS) + r")\b", text):
            other = MONTHS[(MONTHS.index(mt.group()) + 1) % 12]
            add("date", f"{mt.group()} -> {other} at {mt.start()}", text[:mt.start()] + other + text[mt.end():])

        # each merchant in a list answer replaced by another one from the same answer (or a made-up one)
        merchant_slots = [s for s in slots if s["kind"] == "merchant"]
        names = [text[s["start"]:s["end"]] for s in merchant_slots]
        for s, name in zip(merchant_slots, names):
            other = next((n for n in names if n != name), "ACME CORP")
            add("merchant", f"{name} -> {other}", text[:s["start"]] + other + text[s["end"]:])

        # each line dropped (slots after it no longer line up, as with any post-render edit)
        lines = text.split("\n")
        if len(lines) > 1:
            for i, line in enumerate(lines):
                add("drop_line", f"line {i + 1} dropped: {line[:40]!r}", "\n".join(lines[:i] + lines[i + 1:]))

        # a digit or number word in the extra sentence
        for extra in EXTRAS:
            add("extra", extra, text, extra)
    return out


def run() -> tuple[list[Mutation], list[Mutation]]:
    """(all mutations, survivors)."""
    by_name = {c.name: c for c in CASES}
    muts = generate()
    return muts, [m for m in muts if not caught(by_name[m.case], m)]
