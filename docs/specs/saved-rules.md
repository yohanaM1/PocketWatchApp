# Design spec — Saved rules list (Yohana, week of Nov 16)

## Context

The follow-up in [review-queue.md](review-queue.md#follow-up-saved-rules-list-on-the-timeline-week-of-nov-16): a saved
correction ("rule") can't be seen, changed or removed. With Remember on by default in the review queue, a wrong rule
silently categorizes every future import from that merchant, and those rows never come back to review.

### What already exists

- **`category_corrections`**: `id`, `user_id`, `merchant`, `category_id`, unique on `(user_id, merchant)`. No migration.
- **Rules are written** by `apply_merchant_correction` ([services/review.py](../../backend/app/services/review.py)), from
  Remember on the Review page and "apply to others" on the Transactions page. It upserts the rule and recategorizes the
  merchant's other rows not set by hand (`source = correction`).
- **Rules are read** at import by [services/categorization.py](../../backend/app/services/categorization.py): a matching
  merchant gets the rule's category (`source = correction`, confidence 1.0) and never reaches review.
- **Resolving on the Review page marks the queued rows `source = user`** ("as if set by hand"); only the merchant's other
  rows get `correction`. So the source alone can't tell which rows a rule is responsible for.

## Decisions (agreed Nov 19)

1. **A rule's transactions** are the user's transactions from its merchant that are **currently in its category**, whatever
   their source. That's what the list counts, what a change moves and what "send back to review" clears. The merchant's
   rows in *other* categories (the user's deliberate exceptions, e.g. one Amazon order set to Education) are never touched.
   *Why not "never touch rows set by hand":* resolving a merchant on the Review page marks its rows as set by hand, so a
   rule made there would show "0 transactions" and couldn't send anything back to review.
2. **Removing a rule asks what to do with its transactions**, defaulting to **Keep** (the rule was right; I just don't
   want it for new imports). The other choice, **Send back to review**, makes them uncategorized so they appear in the
   review queue (the rule was wrong). Either way, future imports no longer use the rule.
3. **Changing a rule's category** moves its transactions to the new category (`source = correction`) and then applies the
   rule as `apply_merchant_correction` always does (the merchant's other rows not set by hand).
4. **Mixed merchants** (the review queue's `MIXED_MERCHANTS`: AMAZON, WALMART, VENMO, …) are labeled in the list, since a
   one-category rule for them is the risky case the queue warns about.
5. **Recurring payments and flags re-run** after a change or removal, like every other category change.
6. **On the Review page**, below the queue, so the place that creates rules is where they're managed. No new nav tab.

## API

All routes need a login; another user's rule (or a missing id) is 404.

| Route | What it does |
|---|---|
| `GET /corrections` | Your rules, A→Z by merchant: `[{id, merchant, category, transaction_count, mixed}]`. |
| `PATCH /corrections/{id}` with `{category}` | Changes the rule's category (any of the 13). Returns `{rule, updated}`: the rule and how many transactions changed. |
| `DELETE /corrections/{id}?send_to_review=false` | Removes the rule. With `send_to_review=true`, its transactions become uncategorized. Returns `{sent_to_review}`. |

## UI: "Saved rules" on the Review page

```
Saved rules (3)                                                              [Hide]
These categorize future imports from a merchant automatically. Change or remove one that's wrong.

AMAZON        [Shopping   ▾]   12 transactions   Mixed merchant                Remove
STARBUCKS     [Dining     ▾]    9 transactions                                 Remove
  Remove the STARBUCKS → Dining rule?
  (•) Keep its 9 transactions as they are
  ( ) Send its 9 transactions back to review
  [Remove] [Cancel]
VENMO         [Transfers  ▾]    4 transactions   Mixed merchant                Remove
```

- Picking a new category **asks first** ("Change the STARBUCKS rule from Dining to Groceries? This moves its 9
  transactions…") so the old category is spelled out before anything moves; Cancel puts the dropdown back. After
  Change: "Updated 9 transactions" (or "Saved" when none changed).
- After "send back to review", the queue above reloads and shows the merchant again; the nav badge updates.
- Resolving a merchant with Remember on adds its rule to the list without a reload.
- Empty: "No saved rules yet. Choosing Remember when you review a merchant saves one."
- Loading and error states like the rest of the page; the section is collapsible and starts open.

## Tests

- **API** ([test_corrections.py](../../backend/tests/test_corrections.py)): lists only your rules, A→Z, with counts and
  the mixed label; another user's rule is 404 for change and remove; changing moves the rule's rows (including ones
  resolved on the Review page) but not the merchant's rows in other categories; Keep leaves rows as they are; send back
  to review puts them in the queue; a later import no longer uses a removed rule.
- **Frontend** ([SavedRules.test.tsx](../../frontend/src/components/SavedRules.test.tsx)): the list and labels, changing
  a category, both remove choices, the empty state.

## Out of scope

- Adding a rule from this list (rules come from reviewing or from "apply to others").
- Rules for part of a merchant (e.g. "Amazon under $15 is Subscriptions").
- Undo for a change or a removal. An exact undo needs the server to remember which rows it moved and what they were
  (a change-log table and a migration); the confirmation step covers the common slip of picking the wrong item.
