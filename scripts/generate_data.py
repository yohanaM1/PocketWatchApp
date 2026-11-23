"""Generate synthetic bank transactions for a college student.

Writes two files to --out:
  transactions.csv  what a user would upload: date,description,amount
  labels.csv        the answer key (true merchant, category, recurring series, planted anomaly),
                    keyed by row number in transactions.csv. Never import this into the app.

Output is fully determined by --seed, --start, --months and --density.

    python scripts/generate_data.py --months 6 --seed 42 --out data/
"""

import argparse
import csv
import random
import string
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from app.categories import DEFAULT_CATEGORIES  # noqa: E402

CENT = Decimal("0.01")

CITIES = ["COLLEGE PARK MD", "HYATTSVILLE MD", "GREENBELT MD", "SILVER SPRING MD", "WASHINGTON DC"]


@dataclass
class Merchant:
    """A merchant the student visits at random, `per_week` times on average."""

    name: str
    category: str
    descriptions: list[str]  # bank-style templates; placeholders filled by fill_template
    low: float
    high: float
    per_week: float
    weekend_boost: float = 1.0
    sign: int = -1  # -1 = money out, +1 = money in


@dataclass
class Subscription:
    """A fixed-amount charge on (roughly) the same day every month."""

    slug: str
    name: str
    category: str
    description: str
    amount: Decimal
    day: int
    jitter: int = 1  # bank posting can drift +/- this many days
    start_month: int = 0  # index of first month it appears (0-based)
    end_month: int | None = None  # index of last month, or None for ongoing
    price_change: tuple[int, Decimal] | None = None  # (from month index, new amount)


RANDOM_MERCHANTS = [
    # Dining
    Merchant("Starbucks", "Dining", ["SQ *STARBUCKS #{n4} {city}", "STARBUCKS STORE {n5}", "STARBUCKS {n5} {city}"], 3.95, 8.75, 2.5),
    Merchant("Chipotle", "Dining", ["CHIPOTLE {n4}", "CHIPOTLE ONLINE {n4}", "CHIPOTLE MEXICAN GRILL {city}"], 9.85, 15.40, 1.0, 1.3),
    Merchant("Panera Bread", "Dining", ["PANERA BREAD #{n6}", "PANERA BREAD {n6} {city}"], 8.50, 16.25, 0.4),
    Merchant("Taco Bell", "Dining", ["TACO BELL #{n5}", "TACO BELL {n5} {city}"], 5.50, 12.80, 0.5, 1.6),
    Merchant("Dunkin", "Dining", ["DUNKIN #{n6} Q35", "DUNKIN {n6} {city}"], 2.89, 7.45, 0.8),
    Merchant("UMD Dining", "Dining", ["UMD DINING SERVICES", "UMD DINING {n4} COLLEGE PARK MD", "UNIV OF MD DINING"], 7.00, 12.50, 1.5),
    Merchant("DoorDash", "Dining", ["DOORDASH*{word} {ref}", "DD *DOORDASH {word}", "DOORDASH*{word} WWW.DOORDASH. CA"], 17.50, 36.00, 0.5, 2.0),
    Merchant("Uber Eats", "Dining", ["UBER *EATS {ref} HELP.UBER.COM CA", "UBER   *EATS PENDING"], 15.00, 32.00, 0.3, 2.0),
    # Groceries
    Merchant("Giant", "Groceries", ["GIANT {n4} {city}", "GIANT FOOD #{n4}", "GIANT {n4}"], 22.00, 72.00, 0.8, 1.4),
    Merchant("Trader Joe's", "Groceries", ["TRADER JOE S #{n3} {city}", "TRADER JOES #{n3}"], 18.00, 55.00, 0.5, 1.4),
    Merchant("Lidl", "Groceries", ["LIDL #{n4} {city}", "LIDL US {n4}"], 14.00, 40.00, 0.3),
    Merchant("7-Eleven", "Groceries", ["7-ELEVEN {n5}", "7 ELEVEN {n5} {city}"], 2.50, 12.00, 0.6),
    # Transport
    Merchant("Uber", "Transport", ["UBER *TRIP HELP.UBER.COM CA", "UBER   *TRIP {ref}", "UBER *TRIP {ref} HELP.UBER.COM"], 8.50, 26.00, 0.6, 1.8),
    Merchant("Lyft", "Transport", ["LYFT   *RIDE {weekday} {n4}", "LYFT *RIDE {ref}"], 7.80, 22.00, 0.3, 1.8),
    Merchant("WMATA", "Transport", ["WMATA SMARTRIP {n6}", "WMATA-SMARTRIP AUTOLOAD", "SMARTRIP WMATA WASHINGTON DC"], 2.00, 20.00, 1.0, 0.5),
    Merchant("Shell", "Transport", ["SHELL OIL {n8}", "SHELL SERVICE STATION {city}"], 24.00, 46.00, 0.25),
    # Shopping
    Merchant("Amazon", "Shopping", ["AMZN Mktp US*{ref}", "AMAZON.COM*{ref} AMZN.COM/BILL WA", "Amazon.com*{ref}"], 7.99, 64.00, 1.0),
    Merchant("Target", "Shopping", ["TARGET {n8} {city}", "TARGET.COM *{n6}", "TARGET T-{n4}"], 11.00, 62.00, 0.5, 1.4),
    Merchant("Walmart", "Shopping", ["WAL-MART #{n4}", "WM SUPERCENTER #{n4}", "WALMART.COM {ref}"], 9.00, 48.00, 0.2),
    # Entertainment
    Merchant("AMC Theatres", "Entertainment", ["AMC {n4} ONLINE", "AMC THEATRES {city}"], 11.50, 19.00, 0.15, 2.5),
    Merchant("Steam", "Entertainment", ["STEAMGAMES.COM {n10}", "STEAM PURCHASE {ref} WA"], 4.99, 29.99, 0.1),
    Merchant("Ticketmaster", "Entertainment", ["TICKETMASTER {n10}", "TM *TICKETMASTER {ref}"], 38.00, 115.00, 0.05),
    # Education
    Merchant("UMD Bookstore", "Education", ["TERRAPIN TEXTBOOKS COLLEGE PARK", "UMD BOOK CENTER {n4}"], 12.00, 95.00, 0.1),
    # Health
    Merchant("CVS", "Health", ["CVS/PHARMACY #{n5}", "CVS/PHARM {n5} {city}"], 4.50, 26.00, 0.3),
    # Transfers (money out to friends)
    Merchant("Venmo", "Transfers", ["VENMO PAYMENT {n10}", "VENMO *{first} {n10}"], 8.00, 42.00, 0.6, 1.5),
    Merchant("Zelle", "Transfers", ["ZELLE PAYMENT TO {first} {ref}", "ZELLE TO {first}"], 10.00, 60.00, 0.15),
    # Other
    Merchant("ATM", "Other", ["ATM WITHDRAWAL {n6} {city}", "NON-BOA ATM WDRL {city}"], 20.00, 60.00, 0.12),
]

SUBSCRIPTIONS = [
    Subscription("rent", "Landmark Apartments", "Rent", "LANDMARK COLLEGE PARK RENT ACH", Decimal("925.00"), 1, jitter=0),
    Subscription("xfinity", "Xfinity", "Utilities", "COMCAST XFINITY {n10}", Decimal("32.50"), 9),
    Subscription("mint_mobile", "Mint Mobile", "Utilities", "MINT MOBILE {n8} MINTMOBILE.COM CA", Decimal("15.00"), 12),
    Subscription("spotify", "Spotify", "Subscriptions", "Spotify USA {n10}", Decimal("5.99"), 14),
    Subscription("netflix", "Netflix", "Subscriptions", "NETFLIX.COM {n10} CA", Decimal("7.99"), 22,
                 price_change=(3, Decimal("8.99"))),
    Subscription("icloud", "Apple iCloud", "Subscriptions", "APPLE.COM/BILL 866-712-7753 CA", Decimal("2.99"), 3),
    Subscription("prime_student", "Amazon Prime", "Subscriptions", "Prime Video Channels*{ref} amzn.com/bill WA", Decimal("7.49"), 18),
    Subscription("chegg", "Chegg", "Subscriptions", "CHEGG ORDER {n8} SANTA CLARA CA", Decimal("15.95"), 6, start_month=1),
    Subscription("planet_fitness", "Planet Fitness", "Health", "PLANET FITNESS CLUB #{n4}", Decimal("10.00"), 17, end_month=3),
]

FIRST_NAMES = ["JORDAN", "MAYA", "ALEX", "PRIYA", "SAM", "DEVON", "LUIS", "AISHA", "CHRIS", "EMMA"]
WORDS = ["CHICKENSHACK", "SWEETGREEN", "CAVA", "FIVEGUYS", "WINGSTOP", "NOODLESCO", "HALALGUYS"]
WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass
class Row:
    """One generated transaction plus its answer-key labels."""

    date: date
    description: str
    amount: Decimal
    merchant: str
    category: str
    recurring_series: str = ""
    anomaly: str = ""
    sort_key: float = field(default=0.0, repr=False)


# Float -> 2-decimal Decimal
def money(x: float) -> Decimal:
    """Round a float to a Decimal with exactly 2 decimal places (half-up)."""
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


# Fill in a description template
def fill_template(rng: random.Random, template: str, day: date) -> str:
    """Fill a description template's placeholders ({n4}, {ref}, {city}, ...) with random values."""
    # Random digit string
    def digits(n: int) -> str:
        """Return a random string of n digits."""
        return "".join(rng.choice(string.digits) for _ in range(n))

    ref = "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(9))
    return template.format(
        n3=digits(3), n4=digits(4), n5=digits(5), n6=digits(6), n8=digits(8), n10=digits(10),
        ref=ref, city=rng.choice(CITIES), first=rng.choice(FIRST_NAMES), word=rng.choice(WORDS),
        weekday=WEEKDAYS[day.weekday()],
    )


# Month arithmetic
def add_months(d: date, months: int) -> date:
    """Return the first day of the month that is `months` months after d's month."""
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


# Clamp a day to the month's length
def month_day(month_start: date, day: int) -> date:
    """Clamp `day` to the month's length (e.g. day 31 in February)."""
    next_month = add_months(month_start, 1)
    return min(month_start.replace(day=1) + timedelta(days=day - 1), next_month - timedelta(days=1))


# Build all transactions for the period
def generate(seed: int, start: date, months: int, density: float) -> list[Row]:
    """Build every transaction for the period (everyday spending, income, bills, subscriptions, anomalies), sorted by date."""
    rng = random.Random(seed)
    end = add_months(start, months)
    rows: list[Row] = []

    # Random everyday spending.
    day = start
    while day < end:
        weekend = day.weekday() >= 5
        for m in RANDOM_MERCHANTS:
            p = m.per_week / 7 * density * (m.weekend_boost if weekend else 1.0)
            # Textbooks are bought at the start of the term, not all year.
            if m.name == "UMD Bookstore" and (day - start).days < 14:
                p *= 25
            if rng.random() < p:
                amount = money(rng.uniform(m.low, m.high)) * m.sign
                rows.append(Row(day, fill_template(rng, rng.choice(m.descriptions), day), amount, m.name, m.category))
        day += timedelta(days=1)

    # Income: biweekly campus job paycheck (varies with hours) and a monthly transfer from family.
    payday = start + timedelta(days=(4 - start.weekday()) % 7)  # first Friday
    while payday < end:
        rows.append(Row(payday, "UMD PAYROLL DIR DEP PPD ID: {}".format(rng.randint(1000000000, 9999999999)),
                        money(rng.uniform(310, 480)), "UMD Payroll", "Income"))
        payday += timedelta(days=14)
    for i in range(months):
        d = month_day(add_months(start, i), 2)
        rows.append(Row(d, f"VENMO CASHOUT {rng.randint(10**9, 10**10 - 1)}", Decimal("200.00"), "Venmo", "Transfers"))

    # Monthly utilities that vary in amount (not "recurring" in the same-amount sense).
    for i in range(months):
        d = month_day(add_months(start, i), 20)
        rows.append(Row(d, f"PEPCO BILL PAY {rng.randint(10**7, 10**8 - 1)}", -money(rng.uniform(28, 74)), "Pepco", "Utilities"))

    # Planted recurring charges: the answer key for find_recurring.
    for sub in SUBSCRIPTIONS:
        last = months - 1 if sub.end_month is None else min(sub.end_month, months - 1)
        for i in range(sub.start_month, last + 1):
            d = month_day(add_months(start, i), sub.day) + timedelta(days=rng.randint(-sub.jitter, sub.jitter))
            if not (start <= d < end):
                continue
            amount = sub.amount
            if sub.price_change and i >= sub.price_change[0]:
                amount = sub.price_change[1]
            rows.append(Row(d, fill_template(rng, sub.description, d), -amount, sub.name, sub.category, recurring_series=sub.slug))

    plant_anomalies(rng, rows, start, end, count=max(1, months // 2))

    for r in rows:
        r.sort_key = rng.random()
    rows.sort(key=lambda r: (r.date, r.sort_key))
    return rows


# Plant labeled anomalies
def plant_anomalies(rng: random.Random, rows: list[Row], start: date, end: date, count: int) -> None:
    """Add labeled anomalies: duplicate charges, unusually large charges, and unfamiliar merchants."""
    everyday = [r for r in rows if r.category in ("Dining", "Shopping", "Transport", "Groceries")]

    for original in rng.sample(everyday, min(count, len(everyday))):
        rows.append(Row(original.date, original.description, original.amount, original.merchant,
                        original.category, anomaly="duplicate"))

    by_name = {m.name: m for m in RANDOM_MERCHANTS}
    for name in rng.sample(["Chipotle", "Amazon", "Uber", "Target", "DoorDash", "Giant"], min(count, 6)):
        m = by_name[name]
        d = start + timedelta(days=rng.randrange((end - start).days))
        amount = -money(m.high * rng.uniform(4, 8))
        rows.append(Row(d, fill_template(rng, rng.choice(m.descriptions), d), amount, m.name, m.category,
                        anomaly="large_charge"))

    unfamiliar = [
        ("PAYPAL *XJ4HOLDINGS 4029357733", "Xj4 Holdings"),
        ("INTL TXN BZR*DIGITALSVC LONDON GB", "Digitalsvc"),
        ("SP * NOVAGADGETS NOVAGADGETS.CO", "Nova Gadgets"),
    ]
    for desc, name in rng.sample(unfamiliar, min(count, len(unfamiliar))):
        d = start + timedelta(days=rng.randrange((end - start).days))
        rows.append(Row(d, desc, -money(rng.uniform(89, 260)), name, "Other", anomaly="unfamiliar_merchant"))


# Write the CSV and its answer key
def write_outputs(rows: list[Row], out: Path) -> None:
    """Write transactions.csv (the upload format) and labels.csv (the answer key) into `out`."""
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "transactions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "description", "amount"])
        for r in rows:
            w.writerow([r.date.isoformat(), r.description, f"{r.amount:.2f}"])
    with open(out / "labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["row", "date", "description", "amount", "merchant", "category", "recurring_series", "anomaly"])
        for i, r in enumerate(rows, start=1):
            w.writerow([i, r.date.isoformat(), r.description, f"{r.amount:.2f}", r.merchant, r.category,
                        r.recurring_series, r.anomaly])


# Print a summary of what was generated
def print_summary(rows: list[Row]) -> None:
    """Print row counts and totals per category, plus how many recurring series and anomalies were planted."""
    print(f"{len(rows)} transactions, {rows[0].date} to {rows[-1].date}")
    totals: dict[str, Decimal] = {}
    for r in rows:
        totals[r.category] = totals.get(r.category, Decimal(0)) + r.amount
    for cat in DEFAULT_CATEGORIES:
        if cat in totals:
            n = sum(1 for r in rows if r.category == cat)
            print(f"  {cat:<14} {n:>4} rows  {totals[cat]:>10.2f}")
    series = sorted({r.recurring_series for r in rows if r.recurring_series})
    print(f"  recurring series: {len(series)} ({', '.join(series)})")
    for kind in ("duplicate", "large_charge", "unfamiliar_merchant"):
        print(f"  {kind}: {sum(1 for r in rows if r.anomaly == kind)}")


# CLI entry point
def main() -> None:
    """Read the command-line options, check category names, then generate and write the data."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start", default="2026-04", help="first month, YYYY-MM (default 2026-04)")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--density", type=float, default=1.0,
                        help="multiplier on everyday spending frequency (lower = smaller file)")
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()

    unknown = {m.category for m in RANDOM_MERCHANTS} | {s.category for s in SUBSCRIPTIONS}
    unknown -= set(DEFAULT_CATEGORIES)
    if unknown:
        sys.exit(f"Categories not in app/categories.py: {unknown}")

    year, month = map(int, args.start.split("-"))
    rows = generate(args.seed, date(year, month, 1), args.months, args.density)
    write_outputs(rows, args.out)
    print_summary(rows)
    print(f"Wrote {args.out / 'transactions.csv'} and {args.out / 'labels.csv'}")


if __name__ == "__main__":
    main()
