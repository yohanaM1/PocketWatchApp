"""Named periods to inclusive DATE ranges. ET only decides what "today" is; transaction dates are never shifted."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.agent.plan import ExplicitRange

ET = ZoneInfo("America/New_York")
MAX_RANGE_DAYS = 366
MIN_YEAR = 1990  # same lower bound as CSV import dates
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


class PeriodError(ValueError):
    """A period the plan asked for that can't be answered; `reason` is future | reversed | too_long | too_early | order."""

    def __init__(self, reason: str, message: str):
        """Store the machine-readable reason next to the message."""
        super().__init__(message)
        self.reason = reason


def now_et() -> datetime:
    """The current time in ET; injected as a FastAPI dependency so tests can fix it."""
    return datetime.now(ET)


@dataclass(frozen=True)
class ResolvedPeriod:
    start: date
    end: date  # inclusive
    partial: bool  # range ends today and the month isn't over
    label: str  # digit-free words used in the sentence: "in September", "so far this month"

    @property
    def key(self) -> str:
        """Stable id fragment: '2026-09' for whole months, else '2026-09-01_2026-09-19'."""
        if self.start.day == 1 and _month_end(self.start) == self.end:
            return f"{self.start:%Y-%m}"
        return f"{self.start.isoformat()}_{self.end.isoformat()}"


def resolve(period: str | ExplicitRange, today: date) -> ResolvedPeriod:
    """Turn a plan's period into an inclusive date range relative to today (ET)."""
    if isinstance(period, str):
        if period == "this_month":
            return _make(today.replace(day=1), today, today)
        if period == "last_month":
            start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
            return _make(start, _month_end(start), today)
        year, month = map(int, period.split("-"))  # pattern already validated
        if year < MIN_YEAR:  # also stops date(0, …) raising a plain ValueError
            raise PeriodError("too_early", f"{period} is before {MIN_YEAR}")
        start = date(year, month, 1)
        if start > today:
            raise PeriodError("future", f"{period} is in the future")
        return _make(start, min(_month_end(start), today), today)  # current month ends today

    if period.start > period.end:
        raise PeriodError("reversed", "start is after end")
    if period.end > today:
        raise PeriodError("future", "end is in the future")
    if period.start.year < MIN_YEAR:
        raise PeriodError("too_early", f"start is before {MIN_YEAR}")
    if (period.end - period.start).days + 1 > MAX_RANGE_DAYS:
        raise PeriodError("too_long", f"range is longer than {MAX_RANGE_DAYS} days")
    return _make(period.start, period.end, today)


def _make(start: date, end: date, today: date) -> ResolvedPeriod:
    """The one labeling rule, a function of (start, end, today) only. The verifier re-implements it."""
    if start == today.replace(day=1) and end == today:
        return ResolvedPeriod(start, end, partial=True, label="so far this month")
    if start.day == 1 and end == _month_end(start):
        return ResolvedPeriod(start, end, partial=False, label=f"in {MONTHS[start.month - 1]}")
    return ResolvedPeriod(start, end, partial=False, label="in the selected dates")


def _month_end(d: date) -> date:
    """Last day of d's month."""
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def period_json(p: ResolvedPeriod) -> dict:
    """The `period` block every tool result carries; the verifier reads start/end from it."""
    return {"start": p.start.isoformat(), "end": p.end.isoformat(),
            "partial": p.partial, "label": p.label, "key": p.key}
