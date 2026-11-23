"""Code-only checks of the LLM's plan against the question text.

extract_facts runs before any LLM call (unsupported or future questions get a fixed reply);
check_plan runs on each final_plan. The checker reads the question, not the plan, and only
extracts facts it can recognize reliably. Anything it can't parse adds no constraint; the
interpretation line shows the user what was answered.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from app.agent.periods import MONTHS, PeriodError, ResolvedPeriod, resolve
from app.agent.plan import FinalPlan
from app.agent.spending import SPENDING_CATEGORIES

MONTH_ALIASES = {  # lowercase alias -> month number
    **{m.lower(): i + 1 for i, m in enumerate(MONTHS)},
    **{m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)},
    "sept": 9,
}
_alts = "|".join(sorted(MONTH_ALIASES, key=len, reverse=True))
MONTH_RE = re.compile(r"\b(" + _alts + r")\b\.?(?:,?\s+(\d{4})\b)?", re.I)  # "Sept 2026", "September, 2025"
AMBIGUOUS_MONTH_WORDS = {"may", "mar", "march"}  # also verbs ("May I…", "march")

# Context windows around a month mention (text just before / just after it).
PREPOSITION_BEFORE = re.compile(r"\b(in|during|for|of|since|from|until|till|through|thru|before|after|by|"
                                r"between|to|and|early|mid|late)\s+$", re.I)
ADJACENT_RELATIVE = re.compile(r"\b(last|this|next|past)\s+$", re.I)  # "last September": year unclear
RANGE_BEFORE = re.compile(r"\b(since|from|after|before|until|till|through|thru|between|to|starting|"
                          r"early|mid|late|half|week|weekend|first|second)\b[^.?!]{0,15}$", re.I)
RANGE_AFTER = re.compile(r"\s*(\d{1,2}(st|nd|rd|th)?\b|[-–]|to\b|through\b|thru\b|until\b|till\b)", re.I)
LAST_YEAR_AFTER = re.compile(r"\s*(of\s+)?last\s+year\b", re.I)

# "the last month" / "this past month" usually mean the last 30 days, so they aren't matched.
# "this month last year" isn't this month.
RELATIVE_RE = re.compile(r"(?<!the )\b(last month|this month)\b(?!\s+(of\s+)?last\s+year)", re.I)

# Questions M2 has no intent for. A false positive costs one fixed reply; a miss costs a wrong "verified" answer.
UNSUPPORTED_RE = re.compile(
    r"\b(vs\.?|versus|compare[ds]?|comparison|(more|less|fewer)\s+than|increase[ds]?|decrease[ds]?|why|"
    r"trend|average|avg|biggest|largest|smallest|most\s+expensive|budget|income|earn(ed|ings)?|salary|"
    r"paycheck)\b", re.I)

# "other" is left out: it's an everyday word ("other than rent"). Singulars of the exact names are added.
CATEGORY_WORDS = {**{c.lower(): c for c in SPENDING_CATEGORIES if c != "Other"},
                  "grocery": "Groceries", "subscription": "Subscriptions", "utility": "Utilities"}
CATEGORY_RE = re.compile(r"\b(" + "|".join(sorted(CATEGORY_WORDS, key=len, reverse=True)) + r")\b", re.I)
NEGATION_BEFORE = re.compile(r"\b(not|excluding|exclude|except|without|besides|minus|other than|apart from|"
                             r"aside from|outside of)\b(\s+\w+){0,3}\s*$", re.I)
COMPOUND_SKIP = {"Shopping": re.compile(r"\b(grocery|groceries|food)\s+$", re.I)}  # "grocery shopping"


# A period the month/relative-month rules above don't capture still counts as "the question names a period".
OTHER_PERIOD_RE = re.compile(r"\b((this|last|past|previous)\s+(week|year|\d+\s+days?|\d+\s+months?)|since|"
                             r"yesterday|year\s+to\s+date|ytd|in\s+\d{4})\b", re.I)
# "What subscriptions do I have?" is a list_plan question when it names no period.
RECURRING_RE = re.compile(r"\b(subscriptions?|recurring|repeat(ing)?\s+(payments?|charges?|bills?)|"
                          r"monthly\s+(bills?|payments?|charges?))\b", re.I)


class PlanRejected(Exception):
    """The plan contradicts the question; the message goes back to the LLM as the retry reason."""


class Unanswerable(Exception):
    """M2 can't answer this question; the loop returns FIXED_REPLIES[label] as `verified`, with no retry."""

    def __init__(self, label: str):
        """`label` is unsupported | future_period."""
        super().__init__(label)
        self.label = label


@dataclass
class QuestionFacts:
    exact: list[ResolvedPeriod] = field(default_factory=list)  # "in September": plan must equal it
    overlap: list[ResolvedPeriod] = field(default_factory=list)  # "since September": plan must touch it
    include: set[str] = field(default_factory=set)
    exclude: set[str] = field(default_factory=set)


def extract_facts(question: str, today: date, known_merchants: frozenset[str] = frozenset()) -> QuestionFacts:
    """Recognize unsupported questions and pull out the periods and categories the question states."""
    if UNSUPPORTED_RE.search(question):
        raise Unanswerable("unsupported")
    q = question.lower()
    category_names = {c.lower() for c in SPENDING_CATEGORIES}
    for name in known_merchants:  # lowercase, >= 3 chars; CSV-derived, used only for matching
        if name not in category_names and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", q):
            raise Unanswerable("unsupported")  # merchant questions come after M2

    facts = QuestionFacts()
    for m in MONTH_RE.finditer(question):
        word, year = m.group(1).lower(), m.group(2)
        before, after = question[max(0, m.start() - 30):m.start()], question[m.end():m.end() + 20]
        if word in AMBIGUOUS_MONTH_WORDS and not year and not PREPOSITION_BEFORE.search(before):
            continue  # "May I see…": capitalization says nothing at a sentence start
        if not year and ADJACENT_RELATIVE.search(before):
            continue  # "last September", "this March": no constraint
        month = MONTH_ALIASES[word]
        if year:
            y = int(year)
        elif LAST_YEAR_AFTER.match(after):
            y = today.year - 1  # "September last year"
        else:
            y = _most_recent_year(month, today)
        if (y, month) > (today.year, today.month):
            raise Unanswerable("future_period")
        try:
            p = resolve(f"{y:04d}-{month:02d}", today)
        except PeriodError:
            continue  # before 1990: nothing to constrain
        is_range = RANGE_BEFORE.search(before) or RANGE_AFTER.match(after)
        _add(facts.overlap if is_range else facts.exact, p)

    for m in RELATIVE_RE.finditer(question):
        p = resolve("this_month" if m.group(1).lower() == "this month" else "last_month", today)
        _add(facts.overlap if RANGE_BEFORE.search(question[max(0, m.start() - 30):m.start()]) else facts.exact, p)

    if len(facts.exact) >= 2:
        raise Unanswerable("unsupported")  # two different periods = a comparison (after M2)

    for m in CATEGORY_RE.finditer(question):
        cat, before = CATEGORY_WORDS[m.group(1).lower()], question[max(0, m.start() - 40):m.start()]
        if cat in COMPOUND_SKIP and COMPOUND_SKIP[cat].search(before):
            continue
        (facts.exclude if NEGATION_BEFORE.search(before) else facts.include).add(cat)
    facts.include -= facts.exclude
    return facts


def states_period(facts: QuestionFacts, question: str) -> bool:
    """The question names a period: a month, this/last month, or another range ("this year", "since May")."""
    return bool(facts.exact or facts.overlap or OTHER_PERIOD_RE.search(question))


def check_list_plan(facts: QuestionFacts, question: str) -> None:
    """A list answer describes what the user has now; a question about a period gets the fixed `unsupported`."""
    if states_period(facts, question):
        raise Unanswerable("unsupported")


def check_plan(facts: QuestionFacts, question: str, plan: FinalPlan, today: date) -> FinalPlan:
    """Reject plans that contradict what the question states; returns the plan with category_phrase sanitized."""
    if RECURRING_RE.search(question) and not states_period(facts, question):
        raise PlanRejected("the question asks what recurring payments or subscriptions the user has and names no "
                           "period; call list_plan with intent recurring_list instead")
    resolved = resolve(plan.period, today)  # PeriodError is handled by the loop (retry or fixed reply)

    if facts.exact:
        want = facts.exact[0]
        if (want.start, want.end) != (resolved.start, resolved.end):
            raise PlanRejected(f"question asks about {want.start:%B %Y} ({want.start}..{want.end}); "
                               f"plan resolves to {resolved.start}..{resolved.end}")
    for want in facts.overlap:
        if resolved.end < want.start or resolved.start > want.end:
            raise PlanRejected(f"question mentions {want.start:%B %Y}; plan range "
                               f"{resolved.start}..{resolved.end} doesn't include any of it")

    cats = set(SPENDING_CATEGORIES) if plan.categories == "all_spending" else set(plan.categories)
    if facts.include:
        missing = facts.include - cats
        if missing:
            raise PlanRejected(f"question names {', '.join(sorted(missing))}; plan does not include it")
        if len(facts.include) == 1 and cats != facts.include:
            raise PlanRejected(f"question names only {next(iter(facts.include))}; "
                               f"plan should not add other categories")
    if facts.exclude & cats:
        raise PlanRejected(f"question excludes {', '.join(sorted(facts.exclude & cats))}; plan includes it. "
                           f"List the remaining categories instead of all_spending")

    phrase = plan.category_phrase.strip()
    if phrase and not _phrase_ok(phrase, question):
        phrase = ""
    return plan.model_copy(update={"category_phrase": phrase})


def _phrase_ok(phrase: str, question: str) -> bool:
    """Whole words from the question, at least 3 characters, no digits."""
    return (len(phrase) >= 3 and not any(ch.isdigit() for ch in phrase)
            and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", question, re.I) is not None)


def _add(bucket: list[ResolvedPeriod], p: ResolvedPeriod) -> None:
    """Append unless the same range is already there ("last month (September)" is one period, not two)."""
    if all((q.start, q.end) != (p.start, p.end) for q in bucket):
        bucket.append(p)


def _most_recent_year(month: int, today: date) -> int:
    """The year of the most recent occurrence of this month that has started (the current month counts)."""
    return today.year if month <= today.month else today.year - 1
