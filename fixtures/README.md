# Fixtures

A small, hand-checkable dataset for tests and for trying out the agent tools.

- `sample_transactions.csv` is the upload format (`date,description,amount`). It has 63 rows covering Sept–Oct 2026. Negative amounts are money out.
- `sample_labels.csv` is the answer key, joined to the transactions by `row` (1-based, excluding the header). Don't import it into the app.

Regenerate it with the command below. The output is deterministic, so the totals below stay valid unless you change the generator:

```
python3 scripts/generate_data.py --months 2 --density 0.3 --seed 21 --start 2026-09 --out fixtures/
mv fixtures/transactions.csv fixtures/sample_transactions.csv
mv fixtures/labels.csv fixtures/sample_labels.csv
```

## Expected totals (sum of `amount`)

| Category | 2026-09 | 2026-10 |
|---|---:|---:|
| Dining | -61.87 | -82.54 |
| Entertainment | -12.03 | 0.00 |
| Groceries | -161.39 | -105.16 |
| Health | -10.00 | -10.00 |
| Income | 786.45 | 1115.57 |
| Other | 0.00 | -225.56 |
| Rent | -925.00 | -925.00 |
| Shopping | -489.60 | -62.09 |
| Subscriptions | -24.46 | -40.41 |
| Transfers | 200.00 | 181.06 |
| Transport | -89.36 | -73.05 |
| Utilities | -77.74 | -105.50 |
| **All money out** | -1851.45 | -1648.25 |
| **All money in** | 986.45 | 1315.57 |

The demo account (`scripts/seed_demo_user.py`) also gets four October transactions waiting in review and three saved
rules (see the main README, "Demo extras"). They're not in these files and change none of the totals above.

## Planted items

- **Recurring series (9 labeled):** rent, xfinity, mint_mobile, spotify, netflix, icloud, prime_student, planet_fitness, chegg. Chegg starts in October, so it appears only once here and `find_recurring` can't be expected to find it. 8 series are detectable.
- **Duplicate:** the Uber charge of -25.14 on 2026-09-08 appears twice (row 14 is the copy).
- **Large charge:** Amazon -403.43 on 2026-09-06 (row 11).
- **Unfamiliar merchant:** `PAYPAL *XJ4HOLDINGS` -225.56 on 2026-10-24 (row 60).

For the larger evaluation set (529 rows, 6 months), run `python3 scripts/generate_data.py` with no arguments. It writes to `data/`, which is gitignored.
