"""Template snapshots (tests/agent/cases.py) and period unit tests."""

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.agent.periods import resolve
from app.agent.plan import ExplicitRange
from app.agent.templates import _Builder, date_range, money
from tests.agent.cases import CASES, render


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_case_renders_exactly(case):
    """Each branch renders the exact expected text."""
    text, _, _ = render(case)
    assert text == case.expected_text


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_every_digit_is_inside_a_slot(case):
    """No digit is ever template literal text."""
    text, slots, _ = render(case)
    for i, ch in enumerate(text):
        if ch.isdigit():
            assert any(s["start"] <= i < s["end"] for s in slots), (i, text)


def test_literal_with_digit_is_refused():
    """The builder refuses a literal containing a digit."""
    with pytest.raises(AssertionError):
        _Builder().lit("in 2026")


def test_money_and_date_range_properties():
    """Random amounts and ranges: money has commas and 2 decimals; every digit of a range is a date part."""
    rng = random.Random(7)
    for _ in range(300):
        d = Decimal(rng.randint(0, 10**9)) / 100
        s = money(d)
        assert s.startswith("$") and Decimal(s[1:].replace(",", "")) == d
        a = date(2025, 1, 1) + timedelta(days=rng.randint(0, 600))
        z = a + timedelta(days=rng.randint(0, 400))
        r = date_range(a, z)
        assert str(z.year) in r and "–" in r or a == z


def test_month_ends_and_last_month_in_january():
    """Feb in leap/non-leap years; last month in January is December of the previous year."""
    assert resolve("2028-02", date(2028, 6, 1)).end == date(2028, 2, 29)
    assert resolve("2026-02", date(2026, 6, 1)).end == date(2026, 2, 28)
    p = resolve("last_month", date(2027, 1, 3))
    assert (p.start, p.end, p.label, p.key) == (date(2026, 12, 1), date(2026, 12, 31), "in December", "2026-12")


def test_key_format_and_labels():
    """Whole months key as YYYY-MM; anything else as start_end. Labels depend on the dates only."""
    today = date(2026, 10, 19)
    assert resolve("this_month", today).key == "2026-10-01_2026-10-19"
    sep = resolve(ExplicitRange(start=date(2026, 9, 1), end=date(2026, 9, 30), explicit_reason="x"), today)
    assert (sep.key, sep.label) == ("2026-09", "in September")
    assert resolve("2026-10", today).label == "so far this month"
    odd = resolve(ExplicitRange(start=date(2026, 9, 3), end=date(2026, 9, 9), explicit_reason="x"), today)
    assert odd.label == "in the selected dates"


def test_first_of_month_is_one_day():
    """On the 1st, this_month is a single day and renders without a range dash."""
    p = resolve("this_month", date(2026, 10, 1))
    assert (p.start, p.end, p.partial) == (date(2026, 10, 1), date(2026, 10, 1), True)
    assert date_range(p.start, p.end) == "Oct 1, 2026"
