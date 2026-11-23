# Design spec — Recurring payments and anomaly flags (Yohana, week of Nov 9)

## Context

The timeline row for Nov 9 is **`find_recurring`, `detect_anomalies`, and their views**. Today is Nov 14 and **M3 is Nov 19**.
Everything here ships on the `next-task` branch as separate commits, **including agent integration** (Yohana's part; Peniel
reviews the agent and verifier changes, and runs the evaluations after merge). See [Agent integration](#agent-integration).

### What the proposal commits us to

- **Main feature 4:** "See their recurring payments and subscriptions."
- **Main feature 5:** "See flagged unusual or duplicate transactions."
- **Tools table:** `find_recurring()` "finds transactions, such as subscriptions, that repeat regularly from the same merchant
  for the same amount"; `detect_anomalies()` "detects unusual behavior in the transactions, such as outliers and duplicates."
- **Evaluation plan:** recurring payments, "recall on planted subscriptions, with few false matches", target **≥ 85%**.

### What already exists

- **Tables, unused.** `recurring_series` (`user_id`, `merchant`, `typical_amount`, `interval_days`) and `anomaly_flags`
  (`transaction_id`, `reason`, `status` = `open | dismissed | confirmed`) have been in the schema since the first migration.
  No code reads or writes them. Both need new columns (Part 1).
- **No API, no page, no agent support.** Questions that name one of the user's merchants ("When is Netflix due?") are refused
  by the code pre-check before the LLM sees them. Nothing handles "What subscriptions do I have?": "subscription" maps to
  the Subscriptions category, so the LLM may plan a Subscriptions `category_total` for a period the user never asked about.
- **Import keeps duplicates on purpose.** `import_hash` includes the row's nth occurrence in the file, so two identical rows in
  one file are both stored, and re-importing the same file is a no-op ([test_imports.py](../../backend/tests/test_imports.py)).
- **Labeled test data.** `scripts/generate_data.py` writes `labels.csv` with `recurring_series` and `anomaly` columns:

| Set | Recurring series | Anomalies |
|---|---|---|
| Fixture (Sep–Oct, 63 rows; the demo account uses it) | 8 detectable, 2 charges each (Chegg has 1 and can't be found) | 1 duplicate, 1 large charge, 1 unfamiliar merchant |
| Evaluation set (Apr–Sep, 529 rows) | 9: Netflix's price goes from $7.99 to $8.99 in July, Planet Fitness stops after July | 3 of each kind |

- **Traps in the data that a naive rule falls into:** Venmo pays in **+$200.00 every month** (not a bill); Pepco and other
  utilities repeat monthly with **different amounts**; payroll is biweekly with varying amounts; Starbucks and UMD Dining have
  many charges that differ by cents.

## Decisions (agreed Nov 14)

1. **Netflix is one series.** A series is one merchant on one interval; its amount may change over time. The page shows the
   current amount and the change ("was $7.99 until Jul 2026"). This matches `labels.csv`, which labels all six charges `netflix`.
2. **Minimum 2 charges.** The fixture and a user with two months of statements only have 2 per subscription. To keep false
   matches low, the first two charges must be **exactly the same amount**; only an established series may change amount.
3. **Stopped subscriptions stay listed as inactive.** "Stopped" is judged against the user's **latest imported transaction**,
   not today: the evaluation set ends Sep 30 and today is Nov 14, so judging by today would mark everything stopped.
4. **Detection runs after every import**, over all of the user's transactions. Recurring series are deleted and rebuilt.
   Anomaly flags are only added: an existing flag is never removed, re-opened or changed by a re-run.
5. **Pages, APIs and agent integration all ship on this branch for M3.**
6. **Detection also re-runs after category changes** (`PATCH /transactions/{id}`, `POST /review/resolve`). The large-charge
   rule falls back to the category's usual amount, so a category change can add a flag. It's one function call and takes
   milliseconds for a few hundred rows.
7. **Monthly series only for M3.** All planted series are monthly, and 6 months of data can't show a yearly one. Weekly adds
   false-match risk for no tested benefit. Listed in Limitations.
8. **A Flags page with a nav badge for open flags**, the same way Review has one.
9. **Peniel owns the evaluations** (Nov 23 row, after this branch merges): the recurring recall script (`eval_recurring`)
   and re-recording the answer evaluation for the new intents with the course model (`qwen3-small`) after the
   `PROMPT_VERSION` bump. Yohana does all of the agent work. This branch provides what the evaluations need: pure
   detection functions, `transactions.recurring_series_id` for scoring per series, and the new intents' expected plans.

---

## The rules

All three anomaly rules and the recurring rule were prototyped against both labeled sets before writing this:

| Rule | Evaluation set (529 rows) | Fixture (63 rows) |
|---|---|---|
| Recurring | **9 / 9 found, 0 false** (Venmo, Pepco, payroll all rejected) | 8 / 8 found, 0 false |
| Duplicate | 3 / 3, 0 false | 1 / 1, 0 false |
| Large charge | 3 / 3, 0 false | 1 / 1, 0 false |
| Unfamiliar merchant | 3 / 3, 0 false | 1 / 1, 0 false |

The prototype used the generator's true categories. The real app uses the LLM's (and the user's) categories, so the
evaluation script re-measures these after import through the real pipeline.

Common to every rule: only **money out** (`amount < 0`) is ever part of a series or flagged, and only the current user's
transactions across all their accounts are used. Merchants are compared by the stored, cleaned `transactions.merchant`;
rows with no merchant are skipped. The one place money in counts is "seen this merchant before" (unfamiliar merchant).

### Recurring (`find_recurring`)

For each merchant, sort its money-out charges by date and build chains:

- **Start** a series with two charges **26–35 days apart** with **exactly the same amount**.
- **Extend** it with the next charge 26–35 days after the last one at **exactly the current amount**. If several qualify,
  take the one closest to "last charge + 30 days".
- **Price change:** a charge in that window at a different amount within **±20%** of the current amount extends the series
  only if **the next charge repeats the new amount**, or it is the merchant's most recent charge. It then becomes the current
  amount and is recorded as a price change (Netflix: $7.99 → $8.99, +12.5%). This stops a series of same-priced coffees
  from absorbing every later coffee within ±20%.
- Other charges from the same merchant (an Apple app purchase next to iCloud) don't break the chain; they're ignored.
- **Everyday merchants are excluded:** a chain is kept only if its charges are at least **half** of the merchant's
  money-out charges between the chain's first and last date. Starbucks repeats a price 26–35 days apart by chance (two
  false series on the evaluation set without this rule); a subscription is nearly all of what its merchant charges.
- A merchant can have more than one series (e.g. two different monthly amounts from Apple), each started the same way.
- **Interval** = median gap between charges, in days. **Next expected** = last charge + interval.
- **Active** if `last charge + interval + 7 days ≥ the user's latest transaction date`, otherwise **inactive**. The latest
  date is across all accounts, so if one account is imported through October and another only through July, the July
  account's series look stopped (Limitations).
- Only the **most recent** price change is kept (`previous_amount`, `amount_changed_on`).
- **Key:** each series has a stable `key`, `MERCHANT:first-charge-date:first-amount` (e.g. `NETFLIX:2026-04-22:7.99`), so a
  rebuild gives the same series the same key. It changes only if detection changes the series' first charge (e.g. the
  user imports older statements). APIs and chat slots use the key, never the database id.

Why this works on the traps: Venmo is money in; Pepco never has two equal amounts in a row; payroll's amounts vary and its gap
is 14 days; Starbucks' amounts differ by cents and are rarely 26–35 days apart at the same amount.

### Anomalies (`detect_anomalies`)

| Kind | Rule | Which row is flagged | Reason text (example) |
|---|---|---|---|
| `duplicate` | Same account, date, merchant and amount as an earlier row. Same account only: identical charges on two different cards are usually two real purchases | Every row after the first (higher ids); the first is the original | "Same date, merchant and amount as another charge in this account" |
| `large_charge` | At least **5×** the median of the merchant's other charges, when it has ≥ 3 others. Otherwise at least **8×** the median of the category's other charges, when the category has ≥ 3 others (a category mixes very different purchases, so it needs a higher bar). Skip uncategorized rows, Transfers and rows in a recurring series. | That row | "About 6× your usual Amazon charge" |
| `unfamiliar_merchant` | The only row ever from this merchant (any row counts, refunds included: a refund means the user has dealt with them before), at least **$75**, and at least **30 days** of the user's data before it (so a first import isn't full of flags) | That row | "First charge from this merchant, and over $75" |

- The category fallback exists for the demo: the fixture's Amazon has only one other charge (8.4× the Shopping median).
- **Thresholds (5×, 8×, $75) are settings** in `config.py` (`ANOMALY_LARGE_MULTIPLE`, `ANOMALY_LARGE_CATEGORY_MULTIPLE`,
  `ANOMALY_UNFAMILIAR_MIN`), like `REVIEW_CONFIDENCE_THRESHOLD`, so they can be tuned from `.env`. They are tuned to our
  generator; Limitations says so.
- **Measured on the evaluation set plus 8 more generator seeds** (the implemented rules, generator categories):
  recurring 9/9 in every set with 0 false series; duplicates 25/25, large charges 25/25, both with 0 false flags;
  unfamiliar merchants 23/25 (the 2 misses fall in the user's first 30 days) with 2 false flags (first-time Ticketmaster
  concert tickets, $79 and $99). At 5× for the category fallback, Ticketmaster tickets were flagged as large in 4 of 8 seeds.
- A row can have more than one flag (one per kind). NovaGadgets, for example, is unfamiliar *and* 5.8× its category.
- **Re-runs only add flags** that don't exist yet for `(transaction_id, kind)`. A flag, once created, stays until the user
  dismisses or confirms it, even if its rule no longer matches. For example, an unfamiliar merchant that charges a second
  time keeps its flag: a repeat charge from an unknown merchant is more suspicious, not less. Re-runs never change a flag's
  status.

---

## Part 1 — Backend

### Migration

- `recurring_series`: add `key` (VARCHAR, unique per user), `first_date`, `last_date` (DATE), `charge_count` (INT), `previous_amount` (DECIMAL, nullable),
  `amount_changed_on` (DATE, nullable), `active` (BOOL). `typical_amount` holds the current amount.
- `transactions`: add `recurring_series_id` (nullable FK → `recurring_series`, `ON DELETE SET NULL`), so the page can list a
  series' charges and the evaluation can score per series. Rebuilding series sets it again.
- `anomaly_flags`: add `kind` (enum `duplicate | large_charge | unfamiliar_merchant`), `created_at`, and a unique constraint on
  `(transaction_id, kind)`.
- **Schema only.** The migration doesn't run detection, so it never depends on rules that will keep changing.
- **Backfill:** `python -m scripts.refresh_insights` runs detection for every user. It's idempotent, and
  `backend/docker-entrypoint.sh` runs it right after `alembic upgrade head`, so the demo account and any deployed data get
  results without re-importing. `seed_demo_user.py` gets results through the real import endpoint anyway.

### `app/services/insights.py` (new)

- `detect_recurring(rows) -> list[SeriesResult]` and `detect_anomalies(rows, series) -> list[FlagResult]`: pure functions over
  plain row data, no database. These carry all the rules, are what the unit tests hit, and are what the agent tools wrap later.
- `refresh_insights(db, user_id)`: loads the user's rows, runs both, rebuilds `recurring_series`, applies the flag re-run rules.
  Called from `POST /accounts/{id}/imports` after categorization, before the import's final commit, and from decision 6's
  endpoints. Note that the imported rows are already committed by then: `categorize_new_transactions` commits before its
  LLM step.
- **A detection error never fails the request.** `refresh_insights` runs inside a savepoint (`db.begin_nested()`). If it
  raises, only its own changes roll back, the error is logged, and the import (or category change) still returns 200 with
  its normal response. Results catch up on the next import, category change or backfill run.

### `GET /recurring`

```json
{
  "as_of": "2026-09-30",
  "monthly_total": "-1013.91",
  "series": [
    {"key": "NETFLIX:2026-04-22:7.99", "merchant": "NETFLIX", "category": "Subscriptions", "amount": "-8.99", "previous_amount": "-7.99",
     "amount_changed_on": "2026-07-21", "interval_days": 30, "first_date": "2026-04-22", "last_date": "2026-09-21",
     "next_expected": "2026-10-21", "charge_count": 6, "active": true}
  ]
}
```

- The example is the evaluation set: 8 active series (rent included) and Planet Fitness inactive. Dates are illustrative.
- Active series first (largest amount first), then inactive. `monthly_total` is the sum of the active series' current amounts (all series
  are monthly, decision 7).
- `next_expected` is null for inactive series. `category` is the category of the series' latest charge.
- `as_of` is the user's latest transaction date; `active` and `next_expected` are relative to it, not today.
- `GET /recurring/transactions?key=…`: the series' charges, newest first (reuses `transaction_out`). A query parameter,
  because keys contain spaces and colons. Owner-scoped: an unknown key or another user's key is a 404, and the page then
  shows "This list changed. Refresh to see the latest."

### `GET /anomalies` and `PATCH /anomalies/{id}`

- `GET /anomalies?status=open` (default `open`; also `dismissed`, `confirmed`, `all`): each flag with `id`, `kind`, `reason`,
  `status`, `created_at` and the transaction (date, merchant, amount, account, category). Newest transaction first.
  Also returns `open_count` for the nav badge.
- `PATCH /anomalies/{id}` with `{"status": "dismissed" | "confirmed" | "open"}`. Owner-scoped through
  transaction → account → user; another user's flag is a 404, the same as a missing one.

## Part 2 — Frontend

- **Recurring page** (`/recurring`, nav tab "Recurring"):
  - Caption at the top: "Based on transactions through Sep 30, 2026." (`as_of`).
  - Stat tile: "$1,013.91 / month across 8 active payments" (evaluation set; rent is most of it).
  - A next-expected date that's already past today shows as "expected Oct 21 (not imported yet)".
  - Active list: merchant, category chip, amount (with "was $7.99" when the price changed), "every ~30 days",
    last charged, next expected. Expanding a row shows its charges.
  - "Stopped" section, collapsed by default: merchant, amount, "last charged Jul 17".
  - Empty state: "No recurring payments yet. They appear after two monthly charges from the same merchant."
- **Flags page** (`/flags`, nav tab "Flags" with an open-count badge):
  - Filter: Open (default) / Dismissed / Confirmed.
  - One card per flag: kind label ("Possible duplicate", "Unusually large", "New merchant"), the transaction, the reason.
  - Actions on open flags: **Looks fine** (dismiss) and **Not right** (confirm). Confirming only records that the user
    agrees something is wrong; nothing else changes (no totals, no other rows). Dismissed and confirmed cards can be
    reopened. Optimistic update with rollback on error, like the Review page.
- Follow the existing tokens and components (category chips, stat tiles, `AppShell` tabs). Both pages work at phone width.

## Part 3 — Evaluation and docs

- **Evaluation is Peniel's** (decision 9), after this branch merges. What it should measure, for reference:
  `eval_recurring` imports the evaluation set through the real import pipeline, scores recurring recall and false matches
  against `labels.csv` plus per-kind anomaly recall and false flags, and writes `backend/eval/results/recurring.json`.
  This branch only has to make that possible (pure functions, `recurring_series_id`). Score duplicates **per pair**: the
  generator labels whichever copy sorts first, and the rule flags the later one.
- README: the two pages, the rules and thresholds, the new settings, and Limitations: monthly only; thresholds tuned to the
  generator; merchant cleanup keeps location suffixes (e.g. ATM withdrawals in different towns are different merchants);
  "stopped" is relative to the latest import, across all accounts; only the most recent price change is shown; two
  same-price charges 30 days apart (e.g. coffee) count as a series; questions naming a merchant aren't answered in chat.

## Files

| File | Change |
|---|---|
| `backend/alembic/versions/…_recurring_and_flags.py` | New: columns and constraint (schema only) |
| `backend/scripts/refresh_insights.py` | New: backfill detection for every user |
| `backend/docker-entrypoint.sh` | Run `refresh_insights` after migrations |
| `backend/app/models.py` | New columns, `AnomalyKind` enum |
| `backend/app/config.py` | `ANOMALY_LARGE_MULTIPLE` (5), `ANOMALY_UNFAMILIAR_MIN` (75) |
| `backend/app/services/insights.py` | New: rules and `refresh_insights` |
| `backend/app/routers/insights.py` | New: `/recurring`, `/anomalies` |
| `backend/app/routers/imports.py`, `transactions.py`, `review.py` | Call `refresh_insights` |
| `backend/app/schemas.py` | Response and request models |
| `frontend/src/api/insights.ts`, `types.ts` | API client |
| `frontend/src/pages/RecurringPage.tsx`, `FlagsPage.tsx` (+ tests) | New pages |
| `frontend/src/App.tsx`, `components/AppShell.tsx` | Routes, tabs, badge |
| `backend/app/agent/plan.py` | `ListPlan`, tool description, always sent |
| `backend/app/agent/tools/insights.py` | New: `find_recurring`, `detect_anomalies` tool results |
| `backend/app/agent/loop.py` | `list_plan` as a final call, `_finish_list`, no extra sentence |
| `backend/app/agent/plan_check.py` | Period rule for `list_plan`, subscriptions routing |
| `backend/app/agent/templates.py` | `render_recurring_list`, `render_flag_list` |
| `backend/app/agent/verifier.py` | List path, new slot kinds, skeletons, completeness |
| `backend/app/agent/prompts.py`, `replies.py`, `app/config.py` | Prompt line, fixed replies, `PROMPT_VERSION = "m3-1"` |
| `backend/tests/agent/*` | Cases, templates, verifier, mutations, plan check, loop; regenerated UI fixtures |
| `frontend/src/api/types.ts`, `lib/sources.ts`, `components/SourcePanel.tsx`, `AnswerCard.tsx` (+ tests) | New slot kinds, source tables |
| `docs/security/prompt-injection.md` | Trust-boundary row for merchant names in answers |
| `README.md` | Docs and limitations |

## Tests

- **Rules (pure functions):** each planted case and each trap as a small hand-built list: steady monthly, ±1-day drift,
  Netflix price change, Planet Fitness stopping, Chegg's single charge (not found), Venmo money in, Pepco varying, biweekly
  payroll, two same-price coffees 30 days apart (accepted: a true limitation of a 2-charge minimum, documented), exact
  duplicate vs. same amount on a different day, large charge by merchant and by category fallback, unfamiliar under and over $75.
- **Fixture end to end:** import the fixture and check exactly the 8 series and 3 flags from `fixtures/README.md`.
- **Re-runs:** a dismissed flag stays dismissed after another import; an open unfamiliar-merchant flag stays open when the
  merchant gets a second charge; series are rebuilt, not duplicated; `refresh_insights` run twice changes nothing.
- **Rule edges:** a coffee series doesn't absorb later coffees at other prices; a price change that never repeats (and
  isn't the latest charge) doesn't extend the series; a $100 first-time merchant in a user's first 30 days isn't flagged;
  a merchant that refunded the user before isn't "unfamiliar"; three identical rows give two duplicate flags.
- **Keys and failures:** a rebuild keeps every series' key; a detection error inside the import is logged and the import
  still returns 200 with its rows saved.
- **Scoping:** another user's series and flags are invisible, and `PATCH` on them is a 404.
- **Frontend:** both pages render lists and empty states; dismiss/confirm updates and rolls back on error; badge count.
- **Agent:**
  - Known-good answers in `tests/agent/cases.py` for each line type: singular and plural, empty, no data, with and
    without stopped series.
  - Verifier rejects: a wrong or swapped merchant, a swapped amount, a missing, extra or reordered row, a wrong total or
    count, a stopped line that's missing or shouldn't be there, a merchant with `$` or a newline, a digit outside a slot.
  - `test_mutations.py` covers the new cases and the catch rate stays ≥ 90%.
  - Plan check: `list_plan` with a period is `unsupported`; "What subscriptions do I have?" planned as `category_total` is
    rejected; "How much did I spend on subscriptions in September?" stays `category_total`.
  - Loop: `list_plan` with `final_plan` in one reply is retried; no extra-sentence call is made for list intents.
  - Scoping: another user's series and flags never appear in an answer.

## Verification

1. `docker compose up`, log in as the demo user: Recurring shows 8 active series totaling $1,006.96 / month.
2. Flags shows the Uber duplicate, the $403.43 Amazon charge and XJ4HOLDINGS. Dismiss one, re-import the fixture: it stays
   dismissed.
3. Import the 6-month evaluation set as a new user: Netflix shows "$8.99, was $7.99", Planet Fitness is under Stopped.
4. As the demo user on the Ask page: "What subscriptions do I have?" gives a verified list of 8 payments totaling
   $1,006.96 a month, with a Sources table. "Any duplicate charges?" lists the 3 open flags. "How much did I spend on
   subscriptions in October?" is still a category total. "When is Netflix due?" is still refused.
5. After merge (Peniel): `python -m scripts.eval_recurring` reports recall ≥ 85%, and the re-recorded answer evaluation
   still scores the original 30 questions as before.

## Agent integration

On this branch (decision 5).

### Agent decisions (agreed Nov 14)

- **A. Two new intents on a new tool, `list_plan`** (decision E): `recurring_list` (each active series with amount and interval, plus a monthly total
  line) and `flag_list` (each open flag with date, merchant, amount and a fixed label per kind, e.g. "Possible duplicate").
  The stored `reason` text isn't used in chat: it contains numbers ("About 6×", "over $75"), and the verifier rejects any
  digit outside a slot.
- **B. Stopped series are counted, not listed:** after the active list, one line such as "1 payment has stopped (see the
  Recurring page)." Neither intent takes a period: both describe the state as of the user's latest import.
- **C. Questions naming a merchant stay refused** by the code pre-check ("When is Netflix due?"). Listed in Limitations.
- **D. Every row is shown.** The completeness rule is "exactly the rows in the result, in order", with no cut-off.
- **E. A separate tool, not optional fields on `final_plan`.** `final_plan` requires a period and categories, and the
  verifier checks every answer against `plan["resolved"]` and a coverage row. A separate tool leaves `final_plan`, its
  verifier path and its 30 evaluated questions untouched, and avoids `anyOf` in the schema.
- **F. Recurring lines show amount and next expected date;** flag lines show label, merchant, amount and date (exact
  sentences below).
- **G. The Sources panel shows a table** of the rows behind each new answer, like every other answer.
- **H. Answers read the stored results** that `refresh_insights` keeps current, so the chat and the pages can't disagree.
- **I. No exploration tools, and no extra sentence for these intents.** The extra-sentence LLM call sees the answer text,
  and these answers contain merchant names from the CSV. Skipping it keeps merchant text away from the LLM entirely.

### The `list_plan` tool

```python
class ListPlan(Strict):
    intent: Literal["recurring_list", "flag_list"]
```

- Description sent to the LLM: "Call instead of final_plan to list the user's recurring payments and subscriptions
  ('recurring_list') or their open flagged transactions, such as possible duplicates or unusual charges ('flag_list').
  Takes no period: it describes what the user has now. Never write amounts, merchants or dates yourself."
- Added to `TOOL_ARG_MODELS`, `TOOL_DESCRIPTIONS` and `llm_tool_names()`, so it's always sent (no `AGENT_EXPLORATION`).
- **Loop** ([loop.py](../../backend/app/agent/loop.py)): `_run_rounds` treats `list_plan` like `final_plan` as a final
  call. Two different final calls in one reply (`final_plan`, `list_plan`, `no_data_needed`) are contradictory and get the
  same error-and-retry as today. A new `_finish_list` validates the args, runs the plan check, runs the tool server-side,
  renders, verifies, and returns `verified` or `unverified`. No retry on a failed verification, the same as `_finish`.
- **Stored plan:** `{"intent": "recurring_list", "as_of": "<today>", "data_through": "<latest transaction date>"}`.

### Tool results

Built by `app/agent/tools/insights.py`, from the stored tables:

- **`find_recurring`:** `rows` (active series, amount largest first, then merchant A–Z), each with
  `row_id = "series:<key>"`, `merchant`, `amount` (negative, as stored), `next_expected`. Plus `total`
  (`row_id = "rectotal"`, sum of active amounts, count of active series), `stopped` (`row_id = "stopped"`, count) and
  `coverage` (`row_id = "coverage"`, `last_date` = latest transaction date, `count` = the user's transactions).
- **`detect_anomalies`:** `rows` (open flags, transaction date newest first, then flag id), each with
  `row_id = "flag:<id>"`, `kind`, `merchant`, `amount`, `date`. Plus `total` (`row_id = "flagcount"`, count) and
  `coverage`. Flag ids are stable, because re-runs never delete flags.

### Answer text

Lines in order; `{…}` marks a slot. Singular and plural forms are separate sentences.

**`recurring_list`:**

| When | Line |
|---|---|
| The user has no transactions | "You don't have any imported transactions yet, so I can't look for recurring payments." (the whole answer) |
| No active series | "I haven't found any active recurring payments in your transactions." |
| Otherwise, first line | "You have {count} recurring payment(s), {money} a month in total:" |
| One line per active series | "- {merchant}: {money} a month, next expected {date}" |
| Any stopped series | "{count} payment has stopped." / "{count} payments have stopped." + " See the Recurring page for details." |
| Always, except no data | "Based on transactions through {date}." |

**`flag_list`:**

| When | Line |
|---|---|
| The user has no transactions | "You don't have any imported transactions yet, so there's nothing to flag." (the whole answer) |
| No open flags | "You have no open flags." |
| Otherwise, first line | "You have {count} open flag(s):" |
| One line per open flag | "- {flag_label}: {merchant}, {money} on {date}" |
| Any open flags | "Review them on the Flags page." |

Flag labels: `duplicate` → "Possible duplicate", `large_charge` → "Unusually large", `unfamiliar_merchant` → "New merchant".

### New slot kinds

| Kind | Verifier rule (written independently in `verifier.py`) |
|---|---|
| `merchant` | Equals the row's `merchant` exactly; at most 100 characters; no newline, no `$`, no `“”` quotes. A merchant that breaks these makes the answer `unverified`, so it can't pass as text that looks like part of the sentence. |
| `money_charge` | Matches the money format, and equals the absolute value of the row's field (`amount` or `total`). The verifier also re-adds the series amounts itself and checks `rectotal` against its own sum. |
| `flag_label` | Equals the verifier's own copy of the label for the row's `kind`. |

`count` and `date` are reused with their existing rules (`int(row[field])`; `_parse_single` against `row[field]`).

### Verifier path for list intents

`verify()` branches on the plan's intent. Steps 0–2 (slot geometry, every digit inside a slot) and step 6 (every line
matches a skeleton) are shared. Steps 3–5 get a list version, because there's no `plan["resolved"]` period:

- **Step 3–4:** each slot matches its row by the rules above. Unknown `row_id`s fail.
- **Step 5, completeness:**
  - **recurring:** the merchant slots are exactly the result's series, in the result's order, each with one money and one
    date slot from the same row. The first line's count equals the number of series and its money is `rectotal`. The
    stopped line appears if and only if `stopped.count > 0`. The coverage date is `coverage.last_date`.
  - **flags:** the label/merchant slots are exactly the result's open flags, in order, each with one money and one date
    slot from the same row, and the count equals `flagcount`.
  - **no-data and empty answers** contain no money, merchant or row slots.
- **Skeletons:** a new `SKELETONS["recurring_list"]` and `SKELETONS["flag_list"]` with the sentences above.

### Plan check and routing

- **Unchanged:** questions naming one of the user's merchants and `UNSUPPORTED_RE` words stay refused before the LLM (C).
- **`list_plan` with a period in the question** ("What subscriptions did I have in September?") gets the fixed
  `unsupported` reply. These answers describe the present only.
- **"Subscriptions" routing:** a `final_plan` (`category_total` or `transaction_count`) for a question that mentions
  subscriptions or recurring payments **and states no period** is rejected with "use list_plan with recurring_list", so
  the LLM retries once. "How much did I spend on subscriptions in September?" still goes to `category_total`. All 30
  existing evaluation questions name a period, so none of them are affected.
- **Prompt** ([prompts.py](../../backend/app/agent/prompts.py)): one line for `list_plan` ("what recurring payments or
  subscriptions do I have", "how much do my subscriptions cost a month", "any duplicate or unusual charges"), and the
  `unsupported` list no longer covers them. Bump `PROMPT_VERSION` to `m3-1`.
- **Fixed replies** ([replies.py](../../backend/app/agent/replies.py)): `capabilities` and `unsupported` add "list your
  recurring payments, and show transactions flagged as unusual or possible duplicates."

### Frontend

- `SlotKind` in `types.ts` gains `merchant`, `money_charge`, `flag_label`; the answer card highlights them like other slots.
- `sources.ts` / `SourcePanel.tsx`: for `recurring_list`, a table of merchant, amount, next expected (and a "Stopped: N"
  line); for `flag_list`, a table of kind, date, merchant, amount. Built from `final_results`, like today's tables.
- Regenerate the UI fixtures (`python -m tests.agent.write_ui_fixtures`) with one recurring and one flag answer.

### Security note

The trust-boundary table in [prompt-injection.md](../security/prompt-injection.md) gets a row: merchant names now appear in
verified answer text. They're rendered by code, never sent to the LLM (no extra sentence, I), escaped by React, and
limited by the `merchant` slot rule above.

## Out of scope

- Weekly, quarterly and yearly series (decision 7).
- Cancelling a subscription, or reminders before the next charge.
- Learning thresholds per user, or using the LLM to judge anomalies.
- Removing a confirmed duplicate from totals. Confirming a flag only records it; totals don't change.
