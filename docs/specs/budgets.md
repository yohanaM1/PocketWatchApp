# Design spec — Monthly budgets (Yohana, week of Nov 16)

## Context

Main feature 6 in the proposal: **"Set monthly budgets per category and track progress."** It is the last proposal
feature not built at M3 (Nov 19). The timeline puts it in the week of Nov 16 (deployment moved to after M3).

### What already exists

- **The `budgets` table, unused.** `id`, `user_id` → `users`, `category_id` → `categories`, `month` (a DATE, always
  the first of the month), `limit_amount` DECIMAL(12, 2), unique on `(user_id, category_id, month)`. In the schema since
  the first migration, so this feature needs **no migration**.
- **One spending definition** in [app/agent/spending.py](../../backend/app/agent/spending.py): spending for a category is
  −(sum of signed amounts), over the 11 categories that aren't Income or Transfers. A refund lowers spending.
  `counted_category_id()` treats a transaction waiting in the review queue as not categorized yet.
- **Chat refuses budget questions.** "budget" is in the pre-check's refusal list ([plan_check.py](../../backend/app/agent/plan_check.py)),
  so budget questions get the fixed "can't answer that yet" reply. That stays as is (see [Out of scope](#out-of-scope)).

## Decisions (agreed Nov 19)

1. **One limit per category per month.** A budget covers one category in one month and nothing else. Setting Dining for
   October again replaces the limit instead of adding a row (the unique key enforces it). Months don't carry over: a new
   month starts empty, and **Copy last month's budgets** fills it in one click. Editing November never changes October,
   so a past month always shows the limit you had then.
   *Rejected: carry-forward* (a limit applies until changed). Fewer clicks, but deleting November's budget would quietly
   bring October's back, and it's harder to tell which limit applied when.
2. **Only the 11 spending categories can have a budget.** Income and Transfers are rejected with 422.
3. **"Spent" uses the shared spending definition**, including `counted_category_id()`, so a budget's spent figure and a
   verified chat answer for the same category and month can't disagree. Unconfirmed LLM guesses and uncategorized rows
   aren't counted; the page says how many transactions that month are waiting in review and links to the Review page.
4. **The page opens on the month of the latest transaction**, not today's month (the demo data ends in October, so
   November would show $0 everywhere). Same reasoning as `as_of` on the Recurring page. The › arrow stops at the later
   of that month and today's month, so you can still set up the current month before importing it.
5. **Status:** `over` when spent > limit; `near` when spent ≥ 80% of the limit (`BUDGET_NEAR_FRACTION`, default 0.8);
   otherwise `ok`. Spent exactly equal to the limit is `near`, not `over`. `left` = limit − spent (negative when over).

## API

All routes need a login and only ever read or write the current user's budgets. Months are `YYYY-MM`. Money is a
2-decimal string, like every other amount the API returns.

### `GET /budgets?month=2026-10`

`month` is optional; without it, the month of the user's latest transaction (or today's month if there are none).

```json
{
  "month": "2026-10",
  "as_of": "2026-10-30",
  "max_month": "2026-11",
  "budgeted": "1535.00", "spent": "1403.75", "left": "131.25", "over_count": 2, "budget_count": 9,
  "rows": [
    {"category": "Dining", "budget_id": 7, "limit": "80.00", "spent": "82.54", "left": "-2.54", "percent": 103,
     "status": "over", "previous_spent": "61.87"},
    {"category": "Other", "budget_id": null, "limit": null, "spent": "225.56", "left": null, "percent": null,
     "status": "none", "previous_spent": "0.00"}
  ],
  "pending_review_count": 3,
  "previous_month_count": 5
}
```

- `rows` has **all 11 spending categories in the fixed category order**, budgeted or not (`status` `none` = no budget).
- `budgeted`, `spent`, `left`, `over_count` and `budget_count` cover budgeted categories only.
- `percent` is spent / limit × 100 rounded to a whole number (it can pass 100, or be negative after refunds).
- `previous_spent` is the month before's spending, or `null` when nothing was imported for that month (so a first
  month shows "—", not "+$82.54").
- `pending_review_count`: transactions in the month that `needs_review()` holds back from spending.
- `previous_month_count`: how many budgets the month before has (the page shows the copy button when it's > 0).

### `PUT /budgets/{month}/{category}` with `{"limit": "200.00"}`

Creates or replaces one budget and returns its row (the `rows` shape above). The limit must be > 0, ≤ 1,000,000 and
have at most 2 decimal places. An unknown category, Income or Transfers is a 422.

### `DELETE /budgets/{id}`

204. Another user's budget (or a missing id) is 404, so a budget's existence isn't revealed.

### `POST /budgets/copy` with `{"from_month": "2026-09", "to_month": "2026-10"}`

Copies every budget in `from_month` to `to_month`, skipping categories `to_month` already has. Returns
`{"copied": 4, "skipped": 1}`. The same month twice is a 422.

## Budgets page

Redesigned Nov 19 after the first version (status groups and cards) felt unprofessional; the design canvas compared a
spreadsheet ledger, a table with a detail panel, and dial cards, and this combines them. A **Budgets** link sits in the
nav after Recurring.

```
Budgets                                         [‹ October 2026 ›] [Fill blanks from September]
┌ navy banner ──────────────────────────────────────────────────────────────────────────────┐
│ ( 91% )  SPENT $1,403.75 of $1,535.00 · LEFT $131.25 across 9 budgets · OVER 2 Dining, …   │
│ Through Oct 30, 2026. 3 transactions are waiting in review and aren't counted yet. Review │
└───────────────────────────────────────────────────────────────────────────────────────────┘
Category        Budget      Spent      Left          Used     vs Sep
› Dining       [$ 80.00]   $82.54     $2.54 over    (◔) 103%  +$20.67
  └ Sep → Oct $61.87 → $82.54, Up $20.67 (33%) · Largest: Uber Eats $17.64 · 8 largest of 9 purchases · See all
› Groceries    [$120.00]   $105.16    $14.84        (◔) 88%   −$56.23
› Other        [$  Set ]   $225.56    —             ( ) —     +$225.56
Total budgeted  $1,535.00  $1,403.75  $131.25       91%
```

- **Banner (from the dial cards):** a brass dial for the month's percent, then Spent, Left (Over by when negative)
  and the over categories by name; the as-of date and the review notice along the bottom.
- **Table (from the ledger):** all 11 spending categories in the fixed order, so a category never moves; status shows
  as color (ring and Left), not position. A total row closes it.
- **Used:** a small ring in the status color plus the percent, the same dial language as the banner.
- **Budget cell:** Enter or leaving the cell saves and shows "Saved" for 2 s; Esc puts the old value back without
  saving; clearing the cell removes the budget and shows "Removed · Undo" for 6 s. A bad amount shows the message under
  the cell (Enter keeps you editing; leaving the cell puts the old value back and says it wasn't saved).
- **vs last month:** "+$20.67", "−$56.23", "same", or "—" when the month before wasn't imported. Up is red and down is
  green, with no good/bad wording (more Rent isn't a mistake).
- **Detail row (from the detail panel):** clicking a category opens a row under it: last month → this month with the
  change, the largest purchase, and the **8 largest purchases** that count toward Spent (`GET /transactions` with
  `counted=true`, so the list adds up to the Spent figure and never shows rows waiting in review), then "See all N in
  Transactions", which opens the Transactions page filtered to that month and category.
- **Fill blanks from <last month>** shows when last month has budgets and some category this month has none.
- **Phones (≤ 640 px):** Left and vs-last-month columns are hidden; the detail row shows Left instead.
- **No transactions at all:** the banner says "Import a statement to start budgeting." (Setting a budget still works.)

## Tests

- **API** ([test_budgets.py](../../backend/tests/test_budgets.py)): setting a budget twice updates it; every spending
  category is a row in the fixed order; users can't read or delete each other's budgets; Income, Transfers and unknown
  categories are rejected; bad limits are rejected; a refund lowers spent; month boundaries; review-queue guesses aren't
  counted, are reported, and are left out of `counted=true`; `previous_spent` is null when the month before has no
  imports; default month is the latest transaction's; copy skips categories already set.
- **Unit:** status cutoffs at exactly 80% and exactly 100%.
- **Frontend** ([BudgetsPage.test.tsx](../../frontend/src/pages/BudgetsPage.test.tsx)): banner and fixed row order;
  Enter saves and a bad amount doesn't; leaving the cell saves and Esc doesn't; clearing removes with undo; the detail
  row's purchases, change and link; "—" without last month; fill blanks; error and retry.

## Out of scope

- **Budget questions in chat** ("Am I over my dining budget?"). They keep getting the fixed refusal. Adding them needs a
  new tool, a template and verifier checks, which Peniel reviews; possible after M3.
- **Alerts or notifications**, **rollover** of unspent money, and budgets for a group of categories.
