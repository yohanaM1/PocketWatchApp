# PocketWatch Verified Answers: Implementation Proposal (first draft)

Peniel, Oct 19, 2026. Builds on the decisions in `spec.md`. Prose and diagrams only; no code. **Revised Oct 19:** the question UI is in M2 scope (section 9). `spec2-implementation.md` is the build guide.

## 1. Summary and scope

This proposal adds one endpoint, `POST /questions`, and a **question page** in the web app. Together they answer a spending question with a verified answer by M2 (Oct 29). The LLM only turns the question into a **query plan**. Code checks the plan, runs SQL-backed tools, writes the answer from a fixed template, and checks every number again before returning it. The page shows which numbers were verified and where each one came from.

It fits the existing FastAPI backend. The backend work is a new `app/agent/` package, one Alembic migration on the `queries` table, a seed script and a CI workflow. The frontend work is the question page in the React + TypeScript app (section 9). None of Yohana's import or merchant code changes for M2.

- **M2 delivers:** CI, an LLM adapter, `group_by_category`, the plan schema and plan checker, the agent loop, the category-total template, the final verifier, `no_data_needed`, the demo-user seed script, 2–3 tampered cases and the **question UI**.
- **Just after M2 (Oct 30 – Nov 1):** `get_transactions` and the `transaction_count` intent, generated tampered cases, and the baseline demo script. They moved out of M2 to make room for the UI.
- **After M2:** `compare_periods` and its wrapper, merchant cleanup, transfer pairing, recorded LLM responses and the full evaluation set.
- **Not covered here:** the categorization pipeline (Nov 2). It plugs into the existing `categorize_new_transactions` hook and gets its own design.

## 2. Where the system is today

What already exists shapes several choices below.

| Piece | What's there | What it means for this work |
|---|---|---|
| `app/models.py` | Money is `DECIMAL(12,2)`. Amounts are signed (negative = money out). `VerificationStatus` already has `verified` / `unverified` / `failed`. | The verifier compares `Decimal`s exactly, with no float tolerance. The statuses need no schema change. |
| `transactions` | Rows belong to an **account**, not a user. `category_id` is nullable. Indexes on `(account_id, date)` and `category_id`. | Every tool query joins `accounts` and filters on `accounts.user_id`. Uncategorized rows have `category_id IS NULL`. |
| `transactions.date` | A `DATE`, with no time of day. | "ET, fixed" only decides what **today** is. A transaction's own date is never shifted. |
| `app/categories.py` | A fixed list of 13 categories, seeded by the first migration. | **Spending categories** are the 11 that aren't Income or Transfers: Dining, Groceries, Rent, Utilities, Transport, Subscriptions, Shopping, Entertainment, Education, Health, Other. |
| `queries` (`QueryLog`) | `question`, `tool_calls` JSON, `answer`, `verification_status`. | Extended with a migration (section 8) to hold the plan, slot links and the extra sentence. |
| `app/deps.py` | `CurrentUser`, `DbSession`, `get_owned_account`. | The new router uses the same dependencies. Tools take the user from the server, never from the LLM. |
| `csv_import.import_hashes` | The hash is built from (date, amount, raw description, occurrence number in the file). | The seed script can find each labeled fixture row by recomputing that same hash (section 12). |
| Tests | Real MySQL (`pocketwatch_test`), tables built with `create_all`, rolled back after every test. | Agent tests follow the same pattern, using fixture rows inserted in the test. |
| CI, LLM client | Neither exists yet. `requirements.txt` has no LLM SDK. Its `httpx2` package (used by FastAPI's test client) is an HTTP client. | These are the first two M2 items. The LLM adapter uses `httpx2`, so no new HTTP dependency is needed. |
| Frontend | None yet. The proposal's stack is React + TypeScript. Yohana owns the views except the question/answer view. | The question page depends on a frontend scaffold and login (Yohana's, or a minimal one if hers isn't ready by Oct 25). Requests go through a dev proxy, so the backend needs no CORS for M2. |

## 3. Architecture overview

The design has three kinds of component: **untrusted** (the LLM and anything it writes), **deterministic code** (everything that touches numbers), and **storage**. Only code ever writes a number into an answer.

```
                 frontend: question page (React + TypeScript)
                                   |
                         POST /questions  {question}
                                   |
                                   v
 +--------------------------------------------------------------------------+
 |  app/agent/router.py        auth (CurrentUser), opens a QueryLog row     |
 +--------------------------------------------------------------------------+
                                   |
                                   v
 +--------------------------------------------------------------------------+
 |  app/agent/loop.py          orchestrator: rounds, time limit, retry,     |
 |                             final status                                 |
 +---------+-------------------+-------------------+-----------------+------+
           |                   |                   |                 |
           v                   v                   v                 v
 +------------------+ +------------------+ +----------------+ +---------------+
 | llm.py           | | plan_check.py    | | tools/         | | templates.py  |
 | adapter to the   | | code that parses | |  spending.py   | | fixed text +  |
 | course LLM;      | | the question and | |  transactions  | | slots; writes |
 | FakeLLM in tests | | rejects plans    | |                | | slot links    |
 |                  | | that contradict  | | periods.py     | |               |
 |  UNTRUSTED OUT   | | it               | | SQL, user-     | |               |
 +--------+---------+ +------------------+ | scoped         | +-------+-------+
          |                                +-------+--------+         |
          | extra sentence                         |                  |
          | (sees template output only)            v                  v
          |                                +---------------------------------+
          +------------------------------->| verifier.py                     |
                                           | own parser + own sign rules;    |
                                           | every number must sit in a slot |
                                           | and match its raw row value     |
                                           +----------------+----------------+
                                                            |
                                                            v
                                     +--------------------------------------+
                                     | queries row: plan, tool calls +      |
                                     | results, slots, answer, status       |
                                     +--------------------------------------+
```

New files, all under `backend/app/agent/` except where noted:

- `router.py`: `POST /questions`, `GET /questions` (history), `GET /questions/{id}`.
- `loop.py`: the orchestrator.
- `llm.py`: an interface with one method, "send messages and tools, get back tool calls", plus the real client and `FakeLLM`.
- `plan.py`: Pydantic models for every tool's arguments, which together form the plan.
- `plan_check.py`: the code-only check of the plan against the question.
- `periods.py`: turns named periods into date ranges in ET.
- `tools/spending.py` (`group_by_category`) and `tools/transactions.py` (`get_transactions`). `no_data_needed` needs no tool file, because it runs no SQL. The loop returns the fixed reply directly.
- `templates.py`, `verifier.py`, `replies.py` (the fixed replies), `prompts.py`.
- `backend/scripts/seed_demo_user.py` and `.github/workflows/ci.yml` outside the package.
- The question page under `frontend/` (section 9).

## 4. Request lifecycle

One question, from arrival to stored answer. The important property: **the numbers in the answer come from the server re-running the final, checked plan**, not from anything the LLM saw or said during its turns.

```
 user        router        loop           LLM          plan_check     tools(SQL)    template   verifier
  |  question  |             |              |               |              |            |          |
  |----------->| create      |              |               |              |            |          |
  |            | QueryLog    |              |               |              |            |          |
  |            |------------>| round 1: question + tool list,|              |            |          |
  |            |             | tool_choice = required       |              |            |          |
  |            |             |------------->|               |              |            |          |
  |            |             |  tool calls  |               |              |            |          |
  |            |             |<-------------|               |              |            |          |
  |            |             |  (exploration calls run, results go back; at most N rounds)        |
  |            |             |  ... until the LLM calls  final_plan(...)  or  no_data_needed(...) |
  |            |             |------------------------------>|              |            |          |
  |            |             |      ok / rejection reason    |              |            |          |
  |            |             |<------------------------------|              |            |          |
  |            |             |  re-run the checked plan ------------------->|            |          |
  |            |             |<------------------------------------ rows ---|            |          |
  |            |             |  render ------------------------------------------------>|          |
  |            |             |<------------------------------------ text + slot links --|          |
  |            |             |  extra sentence (separate LLM call, template output only)          |
  |            |             |  verify ----------------------------------------------------------->|
  |            |             |<------------------------------------------------- pass / fail -----|
  |            |  answer     | store everything, set status  |              |            |          |
  |<-----------|<------------|              |               |              |            |          |
```

How statuses are decided:

```
                   +---------------------------+
                   |  plan check + verify pass |---------------------> verified
                   +-------------+-------------+
                                 | either fails
                                 v
                   +---------------------------+
                   | one retry: the rejection  |
                   | reason goes back to the   |
                   | LLM as a tool error       |
                   +-------------+-------------+
                   passes   |            | fails again
                 verified <-+            +--> unverified (stored tool data still shown)

  API error, no tool call after retry, round limit, time limit, exception  -----> failed
  no_data_needed chosen (fixed reply, nothing to verify)                   -----> verified
```

Two points to agree with Yohana. First, a fixed reply is marked `verified`, because it has no facts and its text is fixed, so `verified` is honest. Second, an **extra sentence that fails its own check is dropped silently**. It never turns a verified answer into `unverified`, because it carries no facts.

## 5. Query plan and plan checker

### The plan

The LLM sees three kinds of tool:

1. **Exploration tools** (`group_by_category`, `get_transactions`) that it may call to look around. Their results go back to it.
2. **`final_plan`**, whose arguments *are* the plan. Calling it ends the LLM's part.
3. **`no_data_needed`**, which takes only a reply label from a fixed list.

`final_plan`'s arguments for M2, validated by Pydantic in `plan.py`:

- `intent`: an enum. M2 has `category_total`, plus `transaction_count` if `get_transactions` lands in time.
- `period`: a named period (`last_month`, `this_month`, `YYYY-MM`) or `{start, end}` with an `explicit_reason` string.
- `categories`: a list of category names from the fixed enum, or `all_spending`.
- `category_phrase`: the user's own word(s) that were mapped to those categories ("food"). This is shown in the answer as the interpretation.
- `assumed_direction`: `none` for M2. It's used after M2 for "why did X increase".

Pydantic does the schema validation the proposal promised ("all LLM output undergoes schema validation"). An unknown category, a malformed period or an extra field fails **before** any SQL runs. The validation error is sent back to the LLM as the retry message.

### The plan checker

The plan checker is plain code that reads the **question text**, not the plan, and pulls out only what it can recognize reliably:

| What it finds in the question | Rule it enforces on the plan |
|---|---|
| A month name, with or without a year ("September", "Sept 2026") | `period` must be that month. With no year, it's the most recent such month that isn't in the future. |
| "last month" / "this month" / "this past month" | `period` must be the matching named period. |
| An exact category name, case-insensitive ("dining") | That category must be in `categories`. If it's the only category word in the question, it must be the only category in the plan. |
| None of the above | No constraint. The plan's interpretation is shown to the user, not claimed as checked. |

A short worked example:

```
question:  "How much did I spend on dining in September?"
found:     month = 2026-09 (most recent September, as of Oct 19)   category = Dining
plan A:    {intent: category_total, period: "2026-09", categories: [Dining]}   -> pass
plan B:    {intent: category_total, period: "last_month", categories: [Dining]} -> pass
           (last_month resolves to 2026-09 today; the checker compares resolved ranges,
            not spellings)
plan C:    {intent: category_total, period: "2026-09", categories: [Groceries]} -> reject:
           "question names Dining; plan does not include it"
```

The checker deliberately does **not** try to understand "food", "eating out" or "since I moved". Those go to the user as the interpretation line ("Showing Dining and Groceries for Sep 1–30, 2026"). That keeps the checker small enough to test thoroughly.

## 6. Tools, periods and the spending definition

### Period resolution (`periods.py`)

"Today" comes from an injected clock (`now_et()`), fixed in tests. It is computed in `America/New_York`. Named periods resolve to inclusive `DATE` ranges:

- `this_month` is from the 1st to today. It's marked `partial: true`, which the template states ("so far this month").
- `last_month` is the whole previous calendar month.
- `YYYY-MM` is that whole month. A future month is rejected. The current month is treated as `this_month`, so it's also partial.
- An explicit `{start, end}` must have start ≤ end and an end no later than today. It's capped at 366 days.

After M2, a **same-days comparison** helper gives the matching slice of the previous period. Oct 1–19 is compared with Sep 1–19, and the end is clamped to the last day of a shorter month.

### `group_by_category` (`tools/spending.py`)

This is the M2 workhorse. In words:

1. Sum `amount` per `category_id` for the user's transactions in the period, joining `accounts` on `user_id`. One query uses the `(account_id, date)` index.
2. For each of the 11 spending categories, report **spending = −(net sum)**, with a **zero row** for any category that has no transactions. Rank the rows by spending, highest first.
3. Separately report **uncategorized money out**: the count and −sum of rows where `category_id IS NULL AND amount < 0`. Uncategorized positive amounts are ignored.
4. Income and Transfers are never returned as spending.

Each returned row carries a stable `row_id` (e.g. `cat:Dining:2026-09`), the raw signed `net_amount` as a `Decimal` string, the derived `spending`, `count` and `rank`. The verifier checks against the raw value, not the derived one (section 7).

A sketch of the shape of the result:

```
period: 2026-09-01 .. 2026-09-30 (ET), partial: false
rows:
  cat:Rent:2026-09       net -925.00   spending 925.00   count 1   rank 1
  cat:Shopping:2026-09   net -489.60   spending 489.60   count …   rank 2
  ...
  cat:Dining:2026-09     net  -61.87   spending  61.87   count …   rank …
  cat:Education:2026-09  net    0.00   spending   0.00   count 0   rank …   (zero row)
uncategorized: count …, spending …
```

The fixtures don't have an expected count for each category. Tests that need one get it by counting rows in `sample_labels.csv`.

### `get_transactions` (`tools/transactions.py`)

This returns up to a **row cap** of transactions (proposed: 50), sorted by date, and always returns the **total count**. Each row has a `row_id` (`txn:<id>`), date, amount, cleaned `merchant` and category name. It **never** returns `raw_description`. Templates that state a count always use the total count, never the number of rows returned.

### `no_data_needed`

This takes a label from a fixed enum (`greeting`, `capabilities`, `out_of_scope`) and returns the matching fixed text from `replies.py`. The LLM never supplies text. It runs no SQL, so the loop handles it directly instead of through a tool file.

## 7. Templates and the final verifier

### Templates (`templates.py`)

One template per intent. A template is a fixed sentence with named slots. Rendering produces two things: the **text**, and a **slot list** with one entry per slot, holding:

- the character span in the text
- the source `row_id` and field
- the slot's kind (money, count, date range)

For `category_total` (one category):

> You spent **{spending}** on **{category}** {period_label}. *(showing {category} for {date_range})*
> {if uncategorized.count > 0:} {count} transactions ({uncat_spending}) aren't categorized yet and aren't included above.
> {if net_amount > 0:} replaced by: Refunds in {category} exceeded spending by {refund_excess} {period_label}.

Multi-category plans ("food" → Dining + Groceries) list each category on its own line, and **never add them together**, since no tool row holds that sum. A combined total would need a tool field first.

### Final verifier (`verifier.py`)

The verifier is the last step, and it deliberately shares no formatting code with `templates.py`. Rendering bugs and edits made after rendering both have to get past it.

1. **Find every number** in the final text (the template part plus the extra sentence) using its own patterns. That means money (`$1,234.56`), plain integers and dates.
2. **Every number must sit exactly inside a slot span.** A number outside any slot (for example, injected into the extra sentence or added after rendering) fails the answer.
3. **Each slot's number must match its source.** The verifier parses the number back into a `Decimal`, looks up the `row_id` in the **stored tool results** (not the template's copy), and applies its own rule for that kind. For money, the displayed value must equal −`net_amount` for a spending slot. For a count, it must equal `count` or `total_count`. For a date range, it must equal the resolved period.
4. **Every slot must be used exactly once**, and every `row_id` must exist in this question's stored tool results.

That catches the cases from our discussion:

| Tampering | Caught by |
|---|---|
| September's and October's totals swapped in the text | Step 3: each slot's `row_id` points at a specific period's row. |
| A number changed after rendering | Step 3. |
| A digit added to the extra sentence | Step 2. |
| Rounding or sign bug in the renderer (e.g. shows −61.87 or 61.9) | Step 3, because it uses an independent sign and parsing rule. |
| A slot pointing at a row that doesn't exist | Step 4. |

What it **doesn't** catch: a correct number answering the wrong question. That's the plan checker's job (section 5), and the interpretation line covers what the checker can't parse.

### Extra sentence

This is a second LLM call, made only after rendering. Its input is the rendered template text, with **no tool rows and no merchant names**. It must contain no digits and be at most about 200 characters. If it fails, it's dropped, with no retry. It's returned as a separate `extra` field so the UI can label it "not verified".

## 8. Agent loop, limits and storage

### The loop (`loop.py`)

In pseudocode, because the control flow is the point:

```
attempt in 1..2:
    for round in 1..MAX_ROUNDS:
        calls = llm(messages, tools, tool_choice=required)      # API error -> failed
        if no_data_needed in calls: return fixed reply (verified)
        if final_plan in calls: break
        run exploration calls, append results to messages
    else: failed (round limit)

    plan = validate(final_plan args)        # Pydantic; failure -> retry with reason
    plan_check(question, plan)              # failure -> retry with reason
    rows = run tools for plan               # server-side, user-scoped
    text, slots = render(plan, rows)
    extra = maybe_extra_sentence(text)
    if verify(text, extra, slots, rows): verified
    else: retry with reason
unverified, keep rows for display
(the whole thing runs under a TIME_LIMIT; timeout -> failed)
```

Proposed limits, to confirm: `MAX_ROUNDS = 4`, `TIME_LIMIT = 20s` total, row cap 50. M2 handles requests **synchronously**, even with the UI. A normal answer is one LLM call of a few seconds. The page shows a progress state, waits up to 30 seconds, and if the connection drops it finds the stored answer in history (section 9). A background task with polling stays an option if the course LLM turns out to be slow.

### Storage

One migration adds columns to `queries` rather than packing everything into `tool_calls`. That makes history and the verification display easy to query:

| Column | Type | Holds |
|---|---|---|
| `tool_calls` (existing) | JSON | Every tool call with its arguments **and results**, exploration and final, in order. |
| `plan` | JSON, nullable | The final validated plan, plus the resolved period and categories. |
| `answer` (existing) | Text | The template text only. |
| `extra_sentence` | Text, nullable | The unverified extra sentence, if any. |
| `slots` | JSON, nullable | The slot list (span, `row_id`, field, kind). |
| `failure_reason` | String, nullable | Why it ended up `unverified` or `failed`. |
| `attempts` | Integer | 1 or 2. |

History (`GET /questions`) and unverified answers **read these columns and never re-run tools**, so the user always sees the data exactly as it was answered.

`failure_reason` is kept in the database but **never returned by the API**. For an `unverified` answer it holds the rejected text, and that text contains numbers that failed verification. Returning it would make it easy for a view to show an unchecked figure.

## 9. Question UI

The assignment gives Peniel the question/answer view with the verification display, and M2 lists "question, verified answer". So the UI is part of M2. It is one page in the React + TypeScript app. Login, register and CSV import are Yohana's views.

```
 +-----------------------------------------------------------------------+
 |  Ask about your spending                                              |
 |  [ How much did I spend on dining in September?          ] [ Ask ]    |
 |  e.g. "What did I spend on food last month?"                          |
 +-----------------------------------------------------------------------+
 |  [Verified]  Every number here was checked against your transactions.|
 |                                                                       |
 |  You spent $61.87 on Dining in September.                             |
 |            ‾‾‾‾‾‾  <- tap: "Dining · Sep 1 – Sep 30, 2026 · 7 txns"   |
 |  (Showing Dining for Sep 1–30, 2026.)                                 |
 |                                                                       |
 |  AI comment · not verified                                            |
 |  Nice work keeping dining modest.                                     |
 |                                                       [Show sources]  |
 +-----------------------------------------------------------------------+
 |  History                                                              |
 |  How much did I spend on dining in September?   Verified    Oct 19    |
 |  September vs October?                          (reply)     Oct 19    |
 |                                                  [Show older]         |
 +-----------------------------------------------------------------------+
```

**How each outcome looks.** Every badge has a text label, so color is never the only signal.
- **Verified:** a "Verified" badge, the answer with every number marked, and the source row behind each number on tap or focus. The interpretation line is part of the answer text.
- **Fixed reply** (greeting, unsupported question, future period): the text only, with no badge. It contains no facts, so "Verified" would mean nothing.
- **Unverified:** a "Couldn't verify" badge and no written answer. Instead, a table of the source rows straight from SQL (category, period, spending, number of transactions). The extra sentence is never shown.
- **Failed:** a "No answer" badge, the fixed apology and a "Try again" button.
- **Extra sentence:** always in its own block, labeled "AI comment · not verified".
- **Waiting:** a progress message ("this can take up to 20 seconds"), with the box disabled so a question can't be sent twice.

**Rules the page follows:**
- **Numbers come only from the stored answer.** The page never computes or rewords a figure. Number marks come from the slot list, so the page doesn't have to guess which text is a number.
- **Slot positions count Unicode code points** (Python's string indexing). JavaScript counts UTF-16 units, so the page indexes by code point. Otherwise an emoji copied from the question into the interpretation line would shift every mark after it.
- **Everything from the API is shown as plain text, never as HTML.** The extra sentence is LLM text, and the question and phrase are user text. A lint rule in CI enforces this.
- **A lost connection isn't a lost answer.** The server stores every answer before replying. If the request times out after 30 seconds, the page checks history for a newer row with the same question and shows it.
- **History never re-asks.** It reads the stored rows, newest first, 20 at a time.

**Testing.** Component tests run in CI without a backend. They use a fixture file of real answers produced from the backend's template test cases. A backend test fails if that file goes stale, so the UI is always tested against the real slot positions and result shape.

## 10. Security

- **Data scoping.** Tools take `user_id` from `CurrentUser`. No tool argument can name a user or account. Every query joins `accounts.user_id`. This mirrors the `get_owned_account` rule in `app/deps.py`. A test registers two users and checks that one can't see the other's totals.
- **Prompt injection through CSV text.** `raw_description` never leaves the database through any tool. The cleaned `merchant` is still text that came from a CSV, so it's treated as untrusted:
    1. It reaches the LLM only in exploration results.
    2. It reaches the user only through template slots, so it can't change the wording.
    3. It never reaches the extra-sentence call.
  An injected merchant can at most steer the plan, and the plan checker and the interpretation line limit that.
- **Fixed replies.** Text the LLM can't write can't be hijacked. Forcing a tool call means "answer without tools" isn't an available move.
- **The LLM key** is read from `.env` (`LLM_API_KEY`), added to `.env.example` with no value, and never logged. CI doesn't have it.
- **The UI** renders every API string as text, never HTML, and never receives `failure_reason`. Its session token lives in memory and `sessionStorage`, so closing the tab ends the session.

## 11. Testing and evaluation

### Test layers

```
  +---------------------------------------------------------------+
  |  eval harness (after M2): 30 questions, real or recorded LLM  |  answer accuracy,
  |  responses, fixture user                                      |  verified rate, catch rate
  +---------------------------------------------------------------+
  |  loop tests: FakeLLM scripted responses -> full pipeline      |  M2: happy path, retry,
  |  against MySQL with fixture rows                              |  round limit, no_data
  +---------------------------------------------------------------+
  |  unit tests: periods, plan_check, templates, verifier,        |  pure functions, fast,
  |  tampered cases                                               |  most of the cases
  +---------------------------------------------------------------+
  |  tool tests: group_by_category / get_transactions vs.         |  the oracle: fixtures/README
  |  fixtures/README expected totals (sign-flipped)               |  expected totals
  +---------------------------------------------------------------+
  |  UI component tests: answer card per status, number marks,    |  no backend; fixture answers
  |  plain-text rendering, lost-connection recovery               |  generated from the backend
  +---------------------------------------------------------------+
```

- **Tool tests** load `sample_transactions.csv` with labels applied, using the same loader as the seed script. They check every category for both months against the expected totals in `fixtures/README.md` (spending = −expected). They also check zero rows, uncategorized handling, and that one user can't see another's data.
- **Verifier tests** start from one correct rendered answer and apply each kind of tampering in the section 7 table. Each tampered case is a small hand-written change, stored as a data file so the "≥90% caught" count is computed from the files rather than claimed.
- **Loop tests** use `FakeLLM`, which plays back a scripted list of tool calls. For M2 these are hand-written. After M2, `FakeLLM` can play back recorded real responses as well, and the recordings are redone whenever the prompt, the plan schema or a tool's output format changes.
- **UI tests** (section 9) run in CI's frontend job against a checked-in file of real answers. A backend test keeps that file in step with the templates.
- **The LLM tool-calling check** (the timeline's Oct 5 item) is a small script, `backend/scripts/check_llm.py`. It sends one fixture question to the real course LLM and prints the tool calls. It's run by hand, not in CI.

### CI (`.github/workflows/ci.yml`)

GitHub Actions runs on every push and pull request:
1. A MySQL 8.4 service container.
2. Python 3.12 with `pip install -r backend/requirements.txt`.
3. `alembic upgrade head` on an empty database, to catch broken migrations.
4. `pytest` with `TEST_DATABASE_URL` pointed at the service container and a throwaway `JWT_SECRET`.
5. A separate frontend job: Node 20, `npm ci`, typecheck, lint, component tests and a production build.

There are no LLM calls in CI.

## 12. M2 build plan and dependencies

Ten days. The exact order and "done when" for each item are in `spec2-implementation.md` section 7. Cut from the bottom.

| # | Item | Depends on | Rough size |
|---|---|---|---|
| 1 | CI workflow, `llm.py` adapter + `FakeLLM`, plan schemas, `check_llm.py` | Course key | 1 day |
| 2 | `periods.py`, `group_by_category` with zero rows and uncategorized, tool tests | Spending definition agreed with Yohana | 1.5 days |
| 3 | `category_total` template, `verifier.py`, verifier tests, UI fixture answers | 2 | 1.5 days |
| 4 | `plan_check.py` and its tests | 1 | 2 days |
| 5 | `loop.py`, `no_data_needed`, prompts, router, `queries` migration | 3, 4 | 1.5 days |
| 6 | `backend/scripts/seed_demo_user.py`, README notes | Import endpoint (exists) | 0.5 day |
| 7 | 2–3 tampered cases as data files | 3 | 0.25 day |
| 8 | **Question UI** and its tests | 3 (fixtures), 5 (live API), Yohana's scaffold + login | 1.5 days |
| 9 | *Just after M2:* `get_transactions` + `transaction_count` intent | 2 | 1 day |
| 10 | *Just after M2:* generated tampered cases, baseline demo script | 3 | 0.5 day |

Items 1–8 add up to about 9.75 days of work in 10 calendar days, which leaves no slack. Item 8 can start once item 3 has produced the fixture answers, before the loop exists. If Yohana's scaffold isn't ready by Oct 25, a minimal one costs another half day. If something slips:
1. The partial-coverage notes go first.
2. Then the UI's extras: number popovers, the sources toggle on verified answers, history paging.
3. Then item 7 shrinks to one case.

**Seed script.** It goes through the **real HTTP API**: register the demo user, log in, upload `fixtures/sample_transactions.csv`. That way it exercises Yohana's import path unchanged. Then it applies labels directly in the database. It parses the fixture file with `parse_csv`, recomputes each row's hash with `import_hashes`, and matches each hash to `sample_labels.csv` by the label file's `row` column. That gives the transaction to update, with no reliance on insert order. Both rows of the duplicate Uber charge get distinct hashes because the occurrence number is part of the hash. The script refuses to run against the test database.

**Dependencies on Yohana:**
- The spending definition (budgets must match).
- The response shape of the `queries` history for her views.
- Agreement that `queries` gets the new columns.
- The frontend scaffold and login view by Oct 25, which the question page plugs into.
- Where the Oct 26 skeleton deployment serves the frontend: same origin preferred, otherwise an explicit CORS list.
- After M2: the `compare_periods` input and output, and handing over `merchant.py`.

### After M2

```
 Oct 29 M2 ──> merchant cleanup + re-normalize (Peniel) ──> Nov 2 corrections + categorization pipeline
                                                               |
 compare_periods (Yohana) ──> wrapper: direction, $ change,    |
                              % (min base), same-days compare, |
                              premise check, merchant deltas <-+  (needs cleaned merchants)
                                         |
 transfer pairing ───────────────────────+──> eval harness + 30 questions (Nov 9) ──> M3 Nov 19
 recordings for FakeLLM ─────────────────+
```

## 13. Risks and open questions

**Risks**
- **The LLM adapter depends on the course key's API.** Whether it supports a "required tool call" option is unknown until item 1. Fallback: if the reply has no tool call, treat it as a failed round and retry once, as `spec.md` already says.
- **Synchronous requests** may feel slow in the UI if the course LLM is slow. The page shows progress and recovers lost answers from history. The fallback is a background task with polling.
- **The frontend scaffold is a dependency on Yohana.** If it's late, a minimal scaffold costs half a day of Peniel's time.
- **The plan checker's month rule** ("most recent September that isn't in the future") will be wrong for some users' intent in January. The interpretation line shows the resolved range, so the user can see it.
- **Schedule.** About 9.75 days of work in 10, with the UI included. `get_transactions` and the baseline demo already moved past M2, and the cut order above is planned.

**Open questions**
- Is a fixed reply recorded as `verified`, or does it need a fourth status? (This draft says `verified`; `VerificationStatus` has only three values.)
- Are row cap 50, 4 rounds and 20 seconds acceptable?
- Should the verifier also **re-query the database independently** for each `row_id` (stronger, slower), rather than trusting the stored tool results? It would catch tool bugs, not only rendering bugs.
- Does multi-category ("food") ever need a combined total? If so, `group_by_category` needs a combined-total row.
- ~~Who builds the question UI, and how does it show the interpretation line, the "not verified" extra sentence and the status?~~ **Decided:** Peniel, for M2 (section 9).
- Should the question page link to CSV import when the user has no transactions, and should Yohana's transactions view link to the question page?
