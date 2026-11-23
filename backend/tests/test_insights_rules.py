"""The recurring and anomaly rules (app/services/insights.py) on hand-built rows and on the labeled fixture. No database."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path

from app.models import AnomalyKind
from app.services.insights import Row, detect_anomalies, detect_recurring
from app.services.merchant import normalize_merchant

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
_ids = count(1)


def row(d: str, amount: str, merchant: str | None, category: str | None = "Shopping", account: int = 1) -> Row:
    """One row with a fresh id; amounts are strings so they stay exact."""
    return Row(next(_ids), account, date.fromisoformat(d), Decimal(amount), merchant, category)


def monthly(merchant: str, amount: str, start: str, n: int, drift=(0,), category: str = "Subscriptions") -> list[Row]:
    """n charges about a month apart, the day drifting by the given offsets in turn."""
    first = date.fromisoformat(start)
    return [row((first + timedelta(days=30 * i + drift[i % len(drift)])).isoformat(), amount, merchant, category)
            for i in range(n)]


def kinds(flags) -> dict[int, set[AnomalyKind]]:
    """transaction id -> the kinds it was flagged for."""
    out: dict[int, set[AnomalyKind]] = {}
    for f in flags:
        out.setdefault(f.transaction_id, set()).add(f.kind)
    return out


# --- Recurring ---------------------------------------------------------------------------------------------------

def test_steady_monthly_charge_is_one_active_series():
    rows = monthly("SPOTIFY USA", "-5.99", "2026-04-15", 6, drift=(0, 1, -1))
    [s] = detect_recurring(rows)
    assert (s.merchant, s.amount, s.charge_count, s.active) == ("SPOTIFY USA", Decimal("-5.99"), 6, True)
    assert s.key == "SPOTIFY USA:2026-04-15:5.99"
    assert 29 <= s.interval_days <= 31
    assert s.next_expected == s.last_date + timedelta(days=s.interval_days)
    assert s.transaction_ids == tuple(r.id for r in rows)


def test_two_charges_are_enough():
    [s] = detect_recurring(monthly("NETFLIX", "-7.99", "2026-09-23", 2))
    assert s.charge_count == 2


def test_two_charges_must_start_at_exactly_the_same_amount():
    rows = [row("2026-09-10", "-30.24", "PEPCO", "Utilities"), row("2026-10-10", "-58.00", "PEPCO", "Utilities"),
            row("2026-11-10", "-41.10", "PEPCO", "Utilities")]
    assert detect_recurring(rows) == []


def test_price_change_that_repeats_stays_one_series():
    rows = monthly("NETFLIX", "-7.99", "2026-04-21", 3) + monthly("NETFLIX", "-8.99", "2026-07-20", 3)
    [s] = detect_recurring(rows)
    assert (s.amount, s.previous_amount, s.charge_count) == (Decimal("-8.99"), Decimal("-7.99"), 6)
    assert s.amount_changed_on == date(2026, 7, 20)
    assert s.key == "NETFLIX:2026-04-21:7.99"  # the key keeps the first amount


def test_price_change_on_the_latest_charge_is_accepted():
    rows = monthly("NETFLIX", "-7.99", "2026-04-21", 3) + [row("2026-07-20", "-8.99", "NETFLIX")]
    [s] = detect_recurring(rows)
    assert (s.amount, s.charge_count) == (Decimal("-8.99"), 4)


def test_one_off_different_amount_does_not_extend_the_series():
    # A $6.50 charge in the window that never repeats and isn't the latest charge isn't a price change.
    rows = monthly("SPOTIFY USA", "-5.99", "2026-04-15", 2) + [row("2026-06-14", "-6.50", "SPOTIFY USA"),
                                                              row("2026-08-20", "-5.99", "SPOTIFY USA")]
    [s] = detect_recurring(rows)
    assert (s.charge_count, s.amount, s.previous_amount) == (2, Decimal("-5.99"), None)


def test_change_over_twenty_percent_is_not_a_price_change():
    rows = monthly("NETFLIX", "-7.99", "2026-04-21", 3) + monthly("NETFLIX", "-15.49", "2026-07-20", 3)
    series = detect_recurring(rows)
    assert sorted(s.amount for s in series) == [Decimal("-15.49"), Decimal("-7.99")]  # two separate series


def test_stopped_series_is_inactive_relative_to_latest_transaction_not_today():
    gym = monthly("PLANET FITNESS CLUB", "-10.00", "2026-04-17", 4, category="Health")
    later = [row("2026-09-30", "-4.00", "STARBUCKS", "Dining")]  # data runs to Sep 30
    [s] = detect_recurring(gym + later)
    assert s.active is False
    [s] = detect_recurring(monthly("SPOTIFY USA", "-5.99", "2026-04-15", 6))  # ends in September, still active
    assert s.active is True


def test_money_in_is_never_recurring():
    rows = [row("2026-09-02", "200.00", "VENMO", "Transfers"), row("2026-10-02", "200.00", "VENMO", "Transfers")]
    assert detect_recurring(rows) == []


def test_biweekly_payroll_is_not_monthly():
    rows = [row(f"2026-09-{d:02d}", "-400.00", "UMD PAYROLL", "Income") for d in (4, 18)]
    assert detect_recurring(rows) == []


def test_single_charge_is_not_a_series():
    assert detect_recurring([row("2026-10-07", "-15.95", "CHEGG ORDER", "Subscriptions")]) == []


def test_other_charges_from_the_merchant_do_not_break_the_chain():
    icloud = monthly("APPLE", "-2.99", "2026-04-03", 4)
    app = [row("2026-04-20", "-0.99", "APPLE")]
    [s] = detect_recurring(icloud + app)
    assert s.charge_count == 4


def test_two_same_price_coffees_a_month_apart_count_as_a_series():
    # A known limitation of the 2-charge minimum: nothing else from the merchant in between.
    rows = [row("2026-09-05", "-4.75", "BLUE BOTTLE", "Dining"), row("2026-10-05", "-4.75", "BLUE BOTTLE", "Dining")]
    assert len(detect_recurring(rows)) == 1


def test_everyday_merchant_repeating_a_price_is_not_a_series():
    # Same price 30 days apart, but most of the merchant's charges in that time are other amounts.
    rows = [row("2026-07-10", "-7.47", "STARBUCKS", "Dining")]
    rows += [row(f"2026-07-{d}", a, "STARBUCKS", "Dining") for d, a in ((14, "-5.37"), (19, "-6.02"), (25, "-4.10"))]
    rows += [row("2026-08-09", "-7.47", "STARBUCKS", "Dining")]
    assert detect_recurring(rows) == []


def test_a_series_does_not_absorb_later_charges_at_other_prices():
    rows = monthly("SPOTIFY USA", "-5.99", "2026-04-15", 3) + [row("2026-07-15", "-6.20", "SPOTIFY USA"),
                                                              row("2026-08-14", "-6.40", "SPOTIFY USA")]
    [s] = detect_recurring(rows)
    assert s.charge_count == 3


def test_series_are_found_per_merchant():
    rows = monthly("NETFLIX", "-7.99", "2026-04-21", 3) + monthly("SPOTIFY USA", "-5.99", "2026-04-15", 3)
    assert {s.merchant for s in detect_recurring(rows)} == {"NETFLIX", "SPOTIFY USA"}


def test_rows_without_a_merchant_are_skipped():
    rows = [row("2026-09-01", "-10.00", None), row("2026-10-01", "-10.00", None)]
    assert detect_recurring(rows) == []
    assert detect_anomalies(rows, []) == []


def test_empty_input():
    assert detect_recurring([]) == []
    assert detect_anomalies([], []) == []


# --- Anomalies ---------------------------------------------------------------------------------------------------

def history(n: int = 40) -> list[Row]:
    """A month and a half of ordinary spending, so the new-merchant rule has history to go on."""
    return [row((date(2026, 8, 1) + timedelta(days=i)).isoformat(), "-12.00", f"SHOP {i % 5}", "Dining")
            for i in range(n)]


def test_exact_duplicate_flags_every_row_after_the_first():
    a, b, c = (row("2026-09-08", "-25.14", "UBER", "Transport") for _ in range(3))
    flags = kinds(detect_anomalies([a, b, c], []))
    assert flags == {b.id: {AnomalyKind.DUPLICATE}, c.id: {AnomalyKind.DUPLICATE}}


def test_same_amount_on_another_day_or_account_is_not_a_duplicate():
    rows = [row("2026-09-08", "-25.14", "UBER", "Transport"), row("2026-09-09", "-25.14", "UBER", "Transport"),
            row("2026-09-08", "-25.14", "UBER", "Transport", account=2)]
    assert detect_anomalies(rows, []) == []


def test_large_charge_against_the_merchants_usual():
    usual = [row(f"2026-09-{d:02d}", a, "AMAZON") for d, a in ((1, "-30.00"), (8, "-35.00"), (15, "-40.00"))]
    big = row("2026-09-20", "-200.00", "AMAZON")
    [f] = detect_anomalies(usual + [big], [])
    assert (f.transaction_id, f.kind, f.reason) == (big.id, AnomalyKind.LARGE_CHARGE, "About 5× your usual AMAZON charge")


def test_just_under_five_times_is_not_large():
    usual = [row(f"2026-09-{d:02d}", "-40.00", "AMAZON") for d in (1, 8, 15)]
    assert detect_anomalies(usual + [row("2026-09-20", "-199.99", "AMAZON")], []) == []


def test_large_charge_falls_back_to_the_category_at_eight_times():
    others = [row("2026-09-25", "-47.81", "WALMART"), row("2026-09-27", "-38.36", "WALMART"),
              row("2026-10-15", "-62.09", "AMAZON")]
    big = row("2026-09-06", "-403.43", "AMAZON")  # the fixture's planted charge: 8.4x the Shopping median
    [f] = detect_anomalies(others + [big], [])
    assert (f.transaction_id, f.reason) == (big.id, "About 8× your usual Shopping charge")


def test_category_fallback_needs_eight_times_not_five():
    movies = [row(f"2026-09-{d:02d}", "-12.00", f"AMC {d}", "Entertainment") for d in (1, 8, 15)]
    concert = row("2026-09-20", "-90.00", "TICKETMASTER", "Entertainment")  # 7.5x
    assert not any(f.kind is AnomalyKind.LARGE_CHARGE for f in detect_anomalies(movies + [concert], []))


def test_large_charge_skips_uncategorized_transfers_and_series():
    usual = [row(f"2026-09-{d:02d}", "-10.00", "VENMO", "Transfers") for d in (1, 8, 15)]
    assert detect_anomalies(usual + [row("2026-09-20", "-500.00", "VENMO", "Transfers")], []) == []
    usual = [row(f"2026-09-{d:02d}", "-10.00", "SHOP", None) for d in (1, 8, 15)]
    assert detect_anomalies(usual + [row("2026-09-20", "-500.00", "SHOP", None)], []) == []
    rent = monthly("LANDMARK RENT", "-925.00", "2026-09-01", 2, category="Rent")
    small = [row(f"2026-10-{d:02d}", "-50.00", "LANDMARK RENT", "Rent") for d in (5, 10, 15)]  # after the series
    rows = rent + small
    assert detect_anomalies(rows, detect_recurring(rows)) == []


def test_unfamiliar_merchant():
    odd = row("2026-09-20", "-225.56", "XJ4HOLDINGS", "Other")
    [f] = detect_anomalies(history() + [odd], [])
    assert (f.transaction_id, f.kind) == (odd.id, AnomalyKind.UNFAMILIAR_MERCHANT)
    assert f.reason == "First charge from this merchant, and over $75"


def test_unfamiliar_merchant_needs_75_dollars():
    assert detect_anomalies(history() + [row("2026-09-20", "-74.99", "XJ4HOLDINGS", "Other")], []) == []


def test_unfamiliar_merchant_needs_30_days_of_history():
    early = row("2026-08-20", "-225.56", "XJ4HOLDINGS", "Other")  # 19 days after the user's first transaction
    assert detect_anomalies(history() + [early], []) == []


def test_a_refund_means_the_merchant_is_not_unfamiliar():
    rows = history() + [row("2026-08-15", "20.00", "NOVAGADGETS", "Other"), row("2026-09-20", "-256.93", "NOVAGADGETS", "Other")]
    assert detect_anomalies(rows, []) == []


def test_a_row_can_be_flagged_for_two_kinds():
    shopping = [row(f"2026-08-{d:02d}", "-10.00", f"STORE {d}", "Other") for d in (1, 2, 3)]
    odd = row("2026-09-20", "-256.93", "NOVAGADGETS", "Other")  # first-time, and 25x the Other median
    assert kinds(detect_anomalies(history() + shopping + [odd], []))[odd.id] == {
        AnomalyKind.LARGE_CHARGE, AnomalyKind.UNFAMILIAR_MERCHANT}


# --- The labeled fixture ----------------------------------------------------------------------------------------

def test_fixture_finds_exactly_the_planted_series_and_anomalies():
    """fixtures/README.md: 8 detectable series (Chegg has one charge), one anomaly of each kind."""
    with open(FIXTURES / "sample_transactions.csv") as t, open(FIXTURES / "sample_labels.csv") as lab:
        pairs = list(zip(csv.DictReader(t), csv.DictReader(lab)))
    rows = [Row(i, 1, date.fromisoformat(t["date"]), Decimal(t["amount"]), normalize_merchant(t["description"]),
                lab["category"]) for i, (t, lab) in enumerate(pairs, 1)]
    labels = {i: lab for i, (_, lab) in enumerate(pairs, 1)}

    series = detect_recurring(rows)
    assert all(s.active for s in series)
    assert sorted({labels[t]["recurring_series"] for s in series for t in s.transaction_ids}) == sorted(
        {"rent", "xfinity", "mint_mobile", "spotify", "netflix", "icloud", "prime_student", "planet_fitness"})
    assert all(len({labels[t]["recurring_series"] for t in s.transaction_ids}) == 1 for s in series)
    assert sum(-s.amount for s in series) == Decimal("1006.96")

    flags = detect_anomalies(rows, series)
    by_kind = {f.kind: rows[f.transaction_id - 1] for f in flags}
    assert len(flags) == 3
    uber = by_kind[AnomalyKind.DUPLICATE]
    assert (uber.merchant, uber.date, uber.amount) == ("UBER", date(2026, 9, 8), Decimal("-25.14"))
    assert labels[by_kind[AnomalyKind.LARGE_CHARGE].id]["anomaly"] == "large_charge"
    assert labels[by_kind[AnomalyKind.UNFAMILIAR_MERCHANT].id]["anomaly"] == "unfamiliar_merchant"
