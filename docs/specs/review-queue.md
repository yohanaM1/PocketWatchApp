# Design spec — Categorization review queue (Yohana, week of Nov 2)

## Context

The timeline row for Nov 2 is **Categorization review queue and corrections**. Today is Nov 8. The target is to finish by Nov 9, and **M3 is Nov 19**.
This ships on the same branch as the Docker work ([docker-full-stack.md](docker-full-stack.md)).

### What the proposal commits us to

- **Main feature 3:** "Users can review transactions the system was unsure how to categorize."
- **AI architecture:** "If the AI is not confident about a transaction, it is sent to the user for verification."
- **Reliability plan, item 2 (confidence-gated categorization):** "Low-confidence categories are sent to a review queue instead of
  being silently guessed. User corrections are stored and reused to improve future results."
- COURSE_SPEC §5 lists "confidence thresholds with escalation" as a reliability technique. This queue is our escalation path,
  so it supports the reliability part of the grade, not just a UI feature.

### What already exists

- **Corrections storage is done** (transactions view, Oct 27). `PATCH /transactions/{id}` with `apply_to_merchant` upserts a
  `category_corrections` row and recategorizes the merchant's other rows that weren't set by hand. On import,
  `categorize_new_transactions` applies saved corrections first.
- The schema already has everything the queue needs: `transactions.category_confidence` (float),
  `transactions.category_source` (`correction | llm | user | NULL`) and `category_corrections`. **No migration.**
- **Peniel's LLM pipeline is not on main yet.** Until it lands, every imported transaction is uncategorized, so the queue holds
  everything a user hasn't categorized. That's still useful: grouped by merchant, the fixture's 63 rows become a few dozen one-click decisions,
  and every decision is remembered for the next import. When the pipeline lands, the low-confidence guesses show up in the
  queue with no further change on our side.

---

## Decisions (please confirm or change)

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | **What's in the queue** | `category_id IS NULL`, **or** `category_source = llm` with `category_confidence` below the threshold (or NULL). Rows with `user` or `correction` are never in it. | The proposal's two cases: "didn't recognize" and "not confident". A user decision or a saved correction is already trusted. |
| D2 | **Threshold** | `REVIEW_CONFIDENCE_THRESHOLD = 0.7` in `config.py`, overridable from the environment. **0.7 is a placeholder:** the real value is picked from Peniel's evaluation on the 500 labeled transactions, as the lowest cutoff where accepted guesses are about ≥95% correct. A guess with no confidence counts as low. | An LLM's self-reported confidence isn't a calibrated probability (models say "0.9" for wrong answers too), so the cutoff has to come from data. Too high makes review tedious, and too low lets wrong guesses through silently. The calibration also gives the report a concrete reliability result (queue size vs. accuracy of accepted guesses). **Agree it with Peniel** (see Q1). |
| D3 | **Unit of review** | **Group by merchant.** Every transaction has one: `normalize_merchant` always returns a name ([merchant.py](../../backend/app/services/merchant.py)), so there is no "no merchant" case. | The proposal says the LLM categorizes "in groups". One decision per merchant is far less tedious than one per row, and corrections are stored per merchant anyway. |
| D4 | **Resolving a group** | One category for the whole group, plus a **"Remember for future imports"** checkbox, **on by default for normal merchants** (off for mixed ones, D9). Remembering saves the correction and also updates the merchant's other rows that weren't set by hand, exactly like the existing `apply_to_merchant`. | It reuses the corrections path and meets "corrections are stored and reused". It's opt-out because a single-purpose merchant (Starbucks, Pepco) reviewed once is almost always the same category next time. |
| D5 | **Confirming a guess** | Accepting the suggested category (or picking another one) sets `source = user`, `confidence = 1.0`, and the row leaves the queue. | No new column or status: "reviewed" is simply "a person decided". |
| D6 | **Exceptions inside a group** | "Show transactions" expands the group, and each row has its own category select (the existing PATCH, without the merchant prompt). | Covers the occasional Amazon order that's Education rather than Shopping, without a second UI. |
| D7 | **Agent answers** | **Unchanged in this branch.** Low-confidence LLM categories still count in spending totals. | Changing what the agent counts is Peniel's area. See Q2. |
| D9 | **Mixed (multi-purpose) merchants** | A group is **mixed** if any of these holds: (a) the merchant is on a short `MIXED_MERCHANTS` list (AMAZON, WALMART, TARGET, COSTCO, CVS, WALGREENS, VENMO, ZELLE, PAYPAL, APPLE); (b) the queued rows' LLM guesses disagree; (c) the user has already set this merchant's transactions to 2+ different categories. A mixed card shows a "Mixed merchant: usually needs a category per purchase" label, **lists its rows individually by default**, and has **Remember off by default**. "Apply to all" is still available. | Remembering `AMAZON → Shopping` would auto-categorize every future Amazon order as Shopping, and those rows would never come back to review. That's silent wrongness, the thing the queue exists to prevent. Signal (c) learns from the user's own history. |
| D10 | **One charge, several categories** (e.g. one Walmart trip with groceries and clothes) | **Not supported: one category per transaction.** The user picks the category for most of the charge. Documented as a known limitation. | A bank CSV has only date, description and total. Nobody, the AI included, can see the basket. Splits would need a new `transaction_splits` table, and every spending query plus the verifier's row ids would have to read it. That's Peniel's reliability path and 2–3 days of work. It's a post-M3 stretch at most. |
| D8 | **Branch** | **Done:** renamed `feat/docker-full-stack` → `feat/docker-and-review-queue`. | The name should describe both parts of the MR. |

### Questions for Peniel (send before or alongside the MR)

- **Q1, threshold:** what confidence values will the pipeline write, and what cutoff should go to review? Is 0.7 reasonable?
  Does it write the guess into `category_id` (with low confidence) or leave low-confidence rows NULL? This spec handles both.
- **Q2, answers:** should the agent treat below-threshold LLM rows like uncategorized ones ("N transactions are awaiting review")
  rather than counting them silently? The proposal's "instead of being silently guessed" argues yes. That would be a change in
  `tools/spending.py` / `templates.py`, so it's Peniel's decision and a separate MR. This branch gives them the shared `needs_review` predicate to use.

---

## Part 0 — Merchant-name cleanup fixes (first, because the queue groups by merchant)

On the fixture data, one store becomes two merchants:
- `TRADER JOE S #097 …` → `TRADER JOE` (the bank turned the apostrophe into a space, and the lone `S` is dropped as trailing noise),
  but `TRADER JOES #057` → `TRADER JOES`;
- `UNIV OF MD DINING` vs `UMD DINING`.

That means two review cards, and a correction saved for one doesn't match the other.

**Rule changes** in [merchant.py](../../backend/app/services/merchant.py):
1. **Possessives:** apostrophes are removed (`JOE'S` → `JOES`), and a lone `S` token is joined to the word before it
   (`JOE S` → `JOES`).
2. **Alias:** `UNIV OF MD` / `UNIVERSITY OF MARYLAND` → `UMD` (as a prefix, so `UNIV OF MD DINING` → `UMD DINING`).
3. *(Not now)* Stripping local city names (`CHIPOTLE MEXICAN GRILL COLLEGE PARK`). It's specific to this area and needs per-chain aliases.
   Listed under "Merchant name cleanup" in the README.

**Existing data:** `merchant` is computed once at import and stored, and corrections are keyed on it. A new Alembic **data
migration** therefore:
- recomputes `transactions.merchant` from `raw_description` with the new rules;
- renames `category_corrections.merchant`. An old name can't simply be re-normalized, because `TRADER JOE` has already lost its
  `S`. Instead, the old → new mapping is learned **per user from their transactions** (the stored old `merchant` next to the new
  name computed from `raw_description`, using the most common new name). Only a correction with no matching transactions falls back to re-normalizing its name.
  When two of a user's corrections merge into one name, **the newest (highest id) wins** and the others are deleted, so the
  unique `(user_id, merchant)` constraint holds. Renames go through temporary names first, so a chain like A→B, B→C can't collide.
- The logic lives in `app/services/merchant_backfill.py` (`renormalize_merchants(conn)`), so it can be tested directly. The migration calls it.
- `downgrade()` is a no-op: the old names aren't kept, and the rows still work.

The migration imports `normalize_merchant` from the app on purpose, so it uses the rules as they are when it runs. A later rule change
needs its own migration. The duplicate check on re-import uses `import_hash` (raw description), so it isn't affected.

Tests: new `test_merchant.py` cases (`TRADER JOE S` / `TRADER JOE'S` / `TRADER JOES` → `TRADER JOES`, `UNIV OF MD DINING` → `UMD DINING`, existing
cases unchanged, normalizing twice is stable) plus a migration test for the correction merge.

---

## Part 1 — Backend

### `app/services/review.py` (new)

- `needs_review()`: one SQLAlchemy condition for D1, using `config.REVIEW_CONFIDENCE_THRESHOLD`. It's the single definition,
  reused by the review endpoints, the `review` filter on `GET /transactions`, and later by Peniel (Q2).
- `apply_merchant_correction(db, user, merchant, category_id, exclude_ids)`: **extracted from** `update_category` in
  [routers/transactions.py](../../backend/app/routers/transactions.py). It upserts the correction, then updates the user's other rows from this
  merchant that weren't set by hand and aren't already in the category (`source = correction`). It returns how many rows changed. The PATCH endpoint is
  refactored to call it, with no behavior change, and the existing `test_transactions.py` tests guard that.
- `MIXED_MERCHANTS`: the known multi-purpose merchant names (D9a). They match the names `normalize_merchant` produces
  (e.g. `WALMART` from its alias rule). `PAYPAL *SELLER` rows are named after the seller (the prefix is stripped), so
  `PAYPAL` only matches bare PayPal rows. The fixture has AMAZON, WALMART, APPLE and VENMO rows to test with.
- `mixed_reason(merchant, guesses, user_categories)` returns `"known_mixed" | "guesses_differ" | "history_differs" | None`,
  checked in that order (D9). `user_categories` is the set of categories on this user's `source = user` rows for the merchant.

### `GET /review`

Params: `limit` (1–100, default 50), `offset` (≥0). These page over **groups**.

```jsonc
{
  "groups": [
    {
      "merchant": "WALMART",
      "count": 7,
      "reason": "low_confidence",       // "uncategorized" if any row has no category, else "low_confidence"
      // One entry per distinct LLM guess among the queued rows, most common first (ties: higher confidence).
      // `confidence` is the lowest for that category, to stay cautious. Empty when nothing was guessed.
      "guesses": [
        { "category": "Groceries", "count": 5, "confidence": 0.60 },
        { "category": "Shopping",  "count": 2, "confidence": 0.40 }
      ],
      "suggestion": "Groceries",        // guesses[0].category, or null; presets the picker
      "mixed": true,
      "mixed_reason": "known_mixed",    // known_mixed | guesses_differ | history_differs | null (D9)
      "money_out": "41.25", "money_in": "0.00",   // positive 2dp strings, as on the transactions page
      "first_date": "2026-09-03", "last_date": "2026-10-28",
      "examples": [ /* ≤3 newest rows, TransactionOut */ ]
    }
  ],
  "total_groups": 23,
  "total_transactions": 63
}
```

- Every query is owner-scoped (`join(Account).where(Account.user_id == user.id)`), as in `_owned`.
- Sorted by `count desc`, then merchant, so the biggest wins come first.
- Grouping is by `merchant` exactly as stored. The known merchant-name issues (`TRADER JOE S` vs `TRADER JOES`; README
  "Merchant name cleanup") will show up as two groups. That's acceptable here, and the cleanup itself is out of scope.

### `POST /review/resolve`

Body: `{ merchant: str, category: <DEFAULT_CATEGORIES name>, remember: bool = true }`.

1. Sets the user's **queued** rows for that merchant to the category with `source = user`, `confidence = 1.0` (D5).
2. If `remember`, calls `apply_merchant_correction`, excluding the rows from step 1 (D4).
3. Returns `{ updated: int, also_updated: int }`.
4. **404** if the user has no queued rows for that merchant (already resolved, or not theirs; the two look the same). **422** for an unknown category.

Single rows inside a group (D6, and the default view for mixed groups) use the existing `PATCH /transactions/{id}` without `apply_to_merchant`.

### `GET /transactions`: two new optional filters

- `review=true`: only rows matching `needs_review()`. With `limit=1`, `total` is the badge count, so no separate count endpoint is needed.
- `merchant=<name>`: exact match. Used to expand a group (`?merchant=STARBUCKS&review=true`).

### Other changes

- `TransactionOut` gains `category_confidence: float | null` (additive), so the UI can show "Guess: Dining · 55% sure".
- `config.py`: `REVIEW_CONFIDENCE_THRESHOLD`. `.env.example` and `docker-compose.yml` (backend allowlist) pass it through.
- Schemas: `ReviewGroup`, `ReviewPage`, `ReviewResolve`, `ReviewResolveResult` in [schemas.py](../../backend/app/schemas.py).
- New router `routers/review.py`, included in `main.py`.

## Part 2 — Frontend

- **Route + nav:** `/review` behind `RequireAuth`, and a **Review** tab in [AppShell.tsx](../../frontend/src/components/AppShell.tsx) between Transactions and
  Ask, with a **count badge** (`GET /transactions?review=true&limit=1` → `total`), hidden at 0. It refetches on route change and after a resolve.
- **API:** `api/review.ts` (`listReview`, `resolveGroup`), and `review` / `merchant` added to `TransactionFilters`. Types go in `api/types.ts`.
- **`pages/ReviewPage.tsx`:**
  - Header: "**N transactions** from **M merchants** need a category".
  - One card per group:
    - merchant name, count, date range and money out/in;
    - a reason chip, in the category's jewel color:
      - *No category*;
      - *Guess: Dining · 60% sure* when there's one guess;
      - *AI guesses differ: Groceries ×5, Shopping ×2* when there are several;
    - a category `<select>` preset to the suggestion (or "Choose…" with no suggestion, and Apply disabled until a category is picked);
    - a **Remember for future imports** checkbox, checked for normal merchants and unchecked for mixed ones;
    - **[Apply to all N]**.
  - **Mixed groups (D9):**
    - a *Mixed merchant: usually needs a category per purchase* label;
    - the group's rows are expanded by default, each with its own select;
    - "Apply to all" sits below the rows as the secondary action.
  - While a resolve is running, the card is disabled. On success the card leaves the list with an inline confirmation:
    "Set 7 STARBUCKS transactions to Dining · also updated 2 · **remembered for future imports**". The last part appears
    only when Remember was on, so a saved rule is never invisible. On error the card stays and shows the error.
  - **Show transactions** expands the group's rows (via the merchant filter). Each row has a select that PATCHes that one row, which then leaves the group.
  - "Show more groups" uses offset paging.
  - **States:** loading, error with retry, **"All caught up"** (with a link to Transactions), and no transactions at all (pointing to Import).
- **Transactions page:** the "Need a category" tile's link becomes **"Review them"** → `/review`. The uncategorized filter stays
  available in the category dropdown.
- Styles follow the existing Brass & Ink cards, chips and tiles, and the phone layout stacks the card's controls.

## Part 3 — Docs

- README: add `GET /review`, `POST /review/resolve` and the new `GET /transactions` filters to the API table, plus a short "Review queue" section
  (what's queued, the threshold setting, what Remember does, mixed merchants).
- README "Known limitations of the spending definition": add **"One category per transaction.** A purchase that mixes
  categories (one Walmart trip with groceries and clothes) counts entirely in the category you pick. Split transactions aren't supported."
  Also add: "A saved correction for a multi-purpose merchant applies to all its future imports. The review queue avoids saving
  one by default, but the Transactions page's 'apply to others' prompt can still create one."
- Update the `categorize_new_transactions` docstring: the pipeline should write `source = llm` and a confidence, and below-threshold rows show up in review.

---

## Files

- Backend:
  - New: `services/review.py`, `routers/review.py`, `tests/test_review.py`.
  - Changed: `routers/transactions.py` (refactor + filters), `schemas.py`, `config.py`, `main.py`, `services/categorization.py` (docstring).
- Frontend:
  - New: `api/review.ts`, `pages/ReviewPage.tsx`, `pages/ReviewPage.test.tsx`.
  - Changed: `App.tsx`, `components/AppShell.tsx`, `api/transactions.ts`, `api/types.ts`, `pages/TransactionsPage.tsx`, `styles.css`, and
    the tests touched by the nav and tile changes.
- Root: `README.md`, `.env.example`, `docker-compose.yml`.

## Tests

- **`backend/tests/test_review.py`:**
  - **What's queued:** uncategorized ✔; `llm` below the threshold ✔; `llm` with NULL confidence ✔; `llm` at or above the threshold ✘; `user` ✘; `correction` ✘.
  - **Grouping:** count, money out/in, date range, ≤3 newest examples, `reason`, sort order, paging.
  - **Guesses:** one entry per category with its count and lowest confidence, ordered most common first; `suggestion` = the first; empty when nothing was guessed.
  - **Mixed:**
    - a `MIXED_MERCHANTS` name → `known_mixed`;
    - disagreeing guesses → `guesses_differ`;
    - 2+ hand-set categories in the user's history → `history_differs`;
    - a single-purpose merchant with one guess → not mixed;
    - another user's history never makes your merchant mixed.
  - **Resolve:**
    - sets the queued rows to `user`/1.0;
    - with `remember`, saves the correction and updates non-hand-set rows outside the queue, but never `user` rows;
    - without `remember`, saves no correction;
    - a later import of the same merchant is auto-categorized;
    - 404 when nothing is queued, 422 for an unknown category.
  - **Isolation:** another user's queue never appears, and resolving their merchant name changes nothing (404).
  - **Filters:** `review` and `merchant` on `GET /transactions`.
  - The threshold is read from config (monkeypatched).
- **Existing `test_transactions.py`** passes unchanged, proving the refactor didn't change behavior.
- **`frontend/src/pages/ReviewPage.test.tsx`** (fetch mocked as in the other page tests):
  - renders groups and the reason chips (single guess, differing guesses, no category);
  - Apply sends `remember: true` by default and removes the card;
  - a mixed group shows the label, is expanded, and has Remember unchecked (Apply sends `remember: false`);
  - unchecking sends `false`;
  - error keeps the card;
  - expand + per-row PATCH;
  - "All caught up";
  - Apply disabled without a category.
- **AppShell:** the badge shows the count and is hidden at 0.

## Verification

1. `pytest` (all green) and `cd frontend && npm run typecheck && npm run lint && npm test && npm run build`.
2. With **`docker compose up --build`** (this branch's other half), working at `http://localhost:8080`:
   - register → import `fixtures/sample_transactions.csv`, then check that the badge shows 63;
   - on Review, resolve a few merchant groups and check that the badge drops;
   - Transactions shows the new categories;
   - re-import a CSV with new rows from a remembered merchant, and check that they arrive categorized and never enter the queue.
3. **Low-confidence path** (no pipeline yet): set a few rows to `source = llm`, `confidence = 0.4` with `docker compose exec db mysql …`.
   They appear with "Guess: … · 40% sure", and a row at 0.9 doesn't appear. Give two rows from one merchant different guesses,
   and the card shows "AI guesses differ" and is treated as mixed.
4. **Mixed merchant:** the fixture's Walmart/Amazon rows appear expanded, with Remember off. Set them individually, then check that
   no correction was saved and that a re-import puts them back in the queue.
5. Ask "How much did I spend on dining in September?" after reviewing, and the verified answer reflects the reviewed categories.

## Follow-up: Saved rules list (on the timeline, week of Nov 16)

There is no way to **see or delete** a saved correction. A wrong rule picked with Remember on silently categorizes every
future import from that merchant, and those rows never return to review. Today the only fix is to set that merchant again
with "apply to others" on the Transactions page.

Planned follow-up, after recurring/anomalies:
- `GET /corrections` and `DELETE /corrections/{id}` (owner-scoped);
- a "Saved rules" list on the Review page ("STARBUCKS → Dining · Remove").

Removing a rule doesn't change existing rows, only future imports. It's added to the Nov 16 row of the timeline in [PROPOSAL.md](../../PROPOSAL.md).

**Built Nov 19:** see [saved-rules.md](saved-rules.md). Removing a rule now asks whether to keep its transactions (the
default) or send them back to review.

## Out of scope

- **Split transactions** (D10): one charge across several categories. Post-M3 stretch, needs Peniel's agreement on the verifier.
- **Smarter corrections for mixed merchants** (e.g. "Amazon under $15 is Subscriptions"). Corrections are one category per merchant.
- Merchant-name cleanup (separate, known issue).
- Undo for a resolve.
- Changing how the agent counts low-confidence rows (Q2, Peniel's call).
- The LLM pipeline itself (Peniel).
