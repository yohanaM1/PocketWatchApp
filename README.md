# PocketWatch
An app designed to help you watch your finances

## Run everything with Docker

Requires only Docker (with Compose v2).

```
docker compose up --build                # app on http://localhost:8080
docker compose exec backend python -m scripts.seed_demo_user
                                         # optional: the demo user (DEMO_EMAIL / DEMO_PASSWORD in .env) with the fixture data
docker compose exec backend python -m scripts.seed_demo_user --reset
                                         # wipe the demo user's data (imports, rules, budgets, questions) and seed again
```

This starts three containers. `db` is MySQL 8. `backend` applies the migrations on every start (a no-op when they're current) and then runs the API. `web` is nginx: it serves the built React app and forwards `/api/*` to the backend. Only `web` is published. MySQL listens on `127.0.0.1` only, and the backend isn't published at all.

It works from a clean clone with no `.env`. For anything beyond a quick look, `cp .env.example .env` first and set:

- **`JWT_SECRET`** (required for a real deployment). Without it, the backend generates a secret inside the container and logs a warning. Logins survive `docker compose restart` but reset when the container is recreated (`down`/`up`, or a rebuild). A weak value such as `change-me` stops the backend.
- **`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`**. Without them, every question ends as `failed` (see [Verified answers](#verified-answers-post-questions)).
- **`DEMO_EMAIL`** / **`DEMO_PASSWORD`**: the demo login the seed script creates. Your `.env` is where to look it up later. On a deployment this is the graders' test account, so use a strong password there.
- **`WEB_PORT`** / **`MYSQL_PORT`** if 8080 or 3306 is already in use.

Compose reads only the backend's own settings from `.env` (see `docker-compose.yml`). It builds `DATABASE_URL` itself, pointing at the `db` container, so the `127.0.0.1` URL in `.env` is only for host runs.

Useful commands: `docker compose logs -f backend`, `docker compose ps`, `docker compose down` (stop; data is kept in the `db_data` volume), `docker compose down -v` (also deletes the database). After a code change, run `up --build` again.

## Local setup

For development with hot reload. Requires Docker, Python 3.12+ and (for the question UI) Node 20.

```
cp .env.example .env                    # then set JWT_SECRET to a random value
docker compose up -d db                 # MySQL 8 on localhost:3306

cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head                    # create tables and seed categories
uvicorn app.main:app --reload           # API on http://127.0.0.1:8000, try it at /docs
```

Run the tests from `backend/` with `pytest`. They use a separate `pocketwatch_test` database in the same MySQL container, which is created automatically and rolled back after each test.

## API

All endpoints except register/login need an `Authorization: Bearer <token>` header. In `/docs`, use the **Authorize** button and enter your email as the username.

| Endpoint | Description |
|---|---|
| `POST /auth/register` | `{email, password}` (8+ characters). Also creates a default "Checking" account. |
| `POST /auth/login` | Form fields `username` (email) and `password`. Returns `{access_token}`, valid for 24 hours. |
| `GET /auth/me` | The logged-in user. |
| `GET /accounts`, `POST /accounts` | List or create your accounts (`{name, type}`; type is checking, savings, or credit). |
| `POST /accounts/{id}/imports` | Upload a CSV file (form field `file`). Returns `{total_rows, imported, skipped_duplicates, categorized, needs_review}`. |
| `POST /questions` | `{question}` (1–500 characters). Answers a spending question; see [Verified answers](#verified-answers-post-questions). |
| `GET /questions` | Your past questions, newest first. `limit` (1–200, default 50) and `before_id` for the next page. Never re-runs anything. |
| `GET /questions/{id}` | One past question (404 if it isn't yours). |
| `GET /categories` | The 13 category names. |
| `GET /transactions` | Your transactions, newest first, with `total`, `money_out`, `money_in`, `uncategorized_count` and `review_count` (rows waiting in review: uncategorized plus low-confidence guesses). Each transaction has `in_review`: when true, its `category` is only the AI's guess and isn't counted in budgets or answers. Filters: `month` (`YYYY-MM`), `account_id`, `category` (a name or `uncategorized`), `merchant` (exact), `review=true` (only rows in the review queue), `counted=true` (only rows counted in spending, i.e. not waiting in review); `limit` (1–200) / `offset`. |
| `PATCH /transactions/{id}` | `{category, apply_to_merchant?}`. Sets one category by hand; `apply_to_merchant` also saves a correction for the merchant and applies it to its other rows not set by hand. |
| `GET /review` | The review queue grouped by merchant, biggest first; `limit` (1–100) / `offset` page over groups. See [Review queue](#review-queue). |
| `POST /review/resolve` | `{merchant, category, remember?}`. Sets every queued row from the merchant; `remember` (default true) also saves a correction. 404 if nothing from that merchant is queued. |
| `GET /budgets` | One row per spending category for a month: budget (or none), spent, left, percent, status (`ok`, `near`, `over`, `none`) and last month's spending, plus totals and how many transactions are waiting in review. `month` (`YYYY-MM`) defaults to the month of your latest transaction. See [Budgets](#budgets). |
| `PUT /budgets/{month}/{category}` | `{limit}` (> 0, ≤ 1,000,000, 2 decimals). Creates or replaces one budget and returns its row. Income, Transfers and unknown categories are 422. |
| `DELETE /budgets/{id}` | Removes one of your budgets (404 if it isn't yours). |
| `POST /budgets/copy` | `{from_month, to_month}`. Copies one month's budgets into another, skipping categories it already has. Returns `{copied, skipped}`. |
| `GET /corrections` | Your saved rules (category corrections), A→Z by merchant: `{id, merchant, category, transaction_count, mixed}`. See [Saved rules](#saved-rules). |
| `PATCH /corrections/{id}` | `{category}`. Changes a rule's category and moves its transactions with it. Returns `{rule, updated}`. |
| `DELETE /corrections/{id}` | Removes a rule so future imports don't use it. Its transactions keep their category unless `send_to_review=true`, which makes them uncategorized. Returns `{sent_to_review}`. 404 if it isn't yours. |

Users can only reach their own accounts: someone else's account returns 404, the same as one that doesn't exist. New endpoints should look up accounts with `get_owned_account` in `app/deps.py`.

### CSV import format

```
date,description,amount
2026-09-03,SQ *STARBUCKS #1182 COLLEGE PARK MD,-6.45
```

- Dates are `YYYY-MM-DD` or `MM/DD/YYYY`. Amounts are negative for money out, with at most 2 decimals; `$` and commas are allowed.
- The file is at most 1 MB and 5,000 rows. If any row is invalid, nothing is imported and the response lists the bad lines.
- Rows already imported into the account are skipped, so overlapping statements can be uploaded safely. Identical rows within one file (a real double charge) are all kept.
- New transactions are categorized on import; see [Categorization](#categorization).

### Categorization

`categorize_new_transactions` (`app/services/categorization.py`) runs after every import:

1. **Your saved corrections** (from editing a category with "apply to merchant") are applied by merchant name: `source = correction`.
2. **The LLM** (`app/services/llm_categorizer.py`) gets whatever is still uncategorized. Rows are grouped by merchant and direction (money in or out), so each merchant is asked about once. Groups go out 8 per call, 4 calls at a time (the course API's latency varies from seconds to a minute), through a forced `assign_categories` tool call whose category must be one of the 13 fixed names. Each item in the reply is validated separately, and an unusable reply is retried once.
   - Every answered guess is saved as the category, with `source = llm` and the LLM's confidence.
   - Confidence ≥ `REVIEW_CONFIDENCE_THRESHOLD` (default 0.75): the guess is accepted and counts in answers.
   - Below that: the guess waits in the [review queue](#review-queue) as a suggestion, and verified answers report the row as "not categorized yet" instead of counting it (`counted_category_id` in `app/agent/spending.py` uses the queue's own `needs_review()` rule, so the two can't disagree).
   - If the API fails or the 75 s budget (`CATEGORIZE_TIME_LIMIT_S`) runs out, the remaining rows stay uncategorized. The import itself never fails because of the LLM.

Rows set by hand (`source = user`) are never overwritten. Descriptions come from the uploaded file, so they're untrusted. They're cleaned, truncated, and sent only inside a JSON block, and the reply can only name one of the fixed categories. Descriptions that read like instructions to a model are never sent. Money out is never auto-filed as Income, or as Transfers unless the merchant is a known payment app (Venmo, Zelle, Cash App, Apple Cash); those guesses wait for the user, because a description can simply claim to be a transfer. See [docs/security/prompt-injection.md](docs/security/prompt-injection.md) for the attack and the before/after results (`python -m scripts.injection_demo`). Tests never call the real LLM: the test client installs the unconfigured stub.

### Merchant name cleanup (needs review)

On import, each description is turned into a `merchant` name by simple rules in `app/services/merchant.py` (`SQ *STARBUCKS #2411 HYATTSVILLE MD` becomes `STARBUCKS`). Category corrections are matched on this name, so it matters. **This is a first version and should be checked and improved**, for example when the categorization pipeline is built. Possessives are handled (`TRADER JOE'S`, `TRADER JOE S` and `TRADER JOES` are all `TRADER JOES`) and `UNIV OF MD …` is `UMD …`, but:

- City names stay attached when there's no store number in between (`CHIPOTLE MEXICAN GRILL COLLEGE PARK` vs `CHIPOTLE`).
- Other spellings stay different (`GIANT` vs `GIANT FOOD`, `SHELL OIL` vs `SHELL SERVICE STATION SILVER SPRING` in the fixture).
- Only a fixed list of payment-processor prefixes (`SQ *`, `TST*`, `PAYPAL *`, ...) and well-known merchants (Amazon, Uber, Venmo, ...) are recognized.

Names are stored at import. **A rule change needs a data migration** that re-runs the rules over stored rows and moves corrections to the new names: call `renormalize_merchants` from `app/services/merchant_backfill.py`, as `alembic/versions/2026_11_08_3c2078f83051_renormalize_merchant_names.py` does.

### Review queue

`/review` lists the transactions a person should categorize, grouped by merchant (`app/services/review.py`):

- **What's queued:** uncategorized rows, and LLM guesses (`category_source = llm`) with `category_confidence` below `REVIEW_CONFIDENCE_THRESHOLD` (default 0.75, set in `.env`; a guess with no confidence counts as low). Rows you set by hand or that a saved correction set are never queued. **0.75 comes from the categorization evaluation:** on 529 labeled rows, guesses at or above it were 98.8% correct and 4% of rows went to review, while guesses below it were right only 91% of the time. The same setting decides what verified answers count.
- **Resolving a group** sets its queued rows as if you'd set them by hand. **Remember** (on by default) saves a correction, so the merchant's future imports are categorized on arrival and never queued.
- **Mixed merchants** (Amazon, Walmart, Target, CVS, Venmo, …; or the AI's guesses disagree; or you've given the merchant different categories before) are shown one transaction at a time, with Remember off by default, so one category isn't silently applied to every future purchase.
- Cards show the LLM's guess as the suggestion ("Guess: Dining · 60% sure"). A guess the injection defense overruled (money out claimed to be a transfer or income) is stored with no confidence, so it is always queued and shows as "Guess: Transfers" with no percentage. Rows the LLM never saw (held back, or the API failed) say "No category".

### Saved rules

The **Saved rules** section at the bottom of the Review page lists every saved correction ("STARBUCKS → Dining"), so a wrong one can be fixed (`app/routers/corrections.py`; design: [`docs/specs/saved-rules.md`](docs/specs/saved-rules.md)).

- **A rule's transactions** are your transactions from its merchant that are currently in its category, whatever set them (resolving on the Review page marks rows as set by hand, so the source alone can't tell). The merchant's rows in other categories, your deliberate exceptions, are never touched.
- **Changing** a rule's category asks first, naming the old and new category and how many transactions move; future imports use the new category.
- **Removing** a rule asks whether to keep its transactions as they are (the default) or send them back to review. Either way, future imports from that merchant go through normal categorization again.
- Mixed merchants (Amazon, Venmo, …) are labeled, since one category for them is the risky case.

### Recurring payments and flags

`/recurring` lists charges that repeat every month, and `/flags` lists transactions that look unusual (`app/services/insights.py`; design: [`docs/specs/recurring-and-anomalies.md`](docs/specs/recurring-and-anomalies.md)). Both are detected over all of a user's transactions **after every import and category change**. Series are rebuilt each time; flags are only ever added, so a flag you dismissed or confirmed stays that way.

- **Recurring:** money out from one merchant, 26–35 days apart, starting with two charges at exactly the same amount. A price change counts when the new amount repeats (Netflix $7.99 → $8.99 is one series). A series must be at least half of what the merchant charged over that time, so a coffee shop repeating a price by chance isn't a subscription. "Stopped" and "next expected" are judged against your latest imported transaction, not today.
- **Flags:** a **possible duplicate** (same account, date, merchant and amount as an earlier row), an **unusually large** charge (5× the merchant's usual, or 8× the category's when the merchant has fewer than 3 other charges), and a **new merchant** (the only row ever from it, at least $75, with 30 days of your history before it). Tune them with `ANOMALY_LARGE_MULTIPLE`, `ANOMALY_LARGE_CATEGORY_MULTIPLE` and `ANOMALY_UNFAMILIAR_MIN` in `.env`. "Looks fine" dismisses a flag; "Not right" confirms it, which only records your answer.
- **Measured** on the 529-row evaluation set plus 8 more generator seeds: every planted series found with no false series; every planted duplicate and large charge found with no false flags; 23 of 25 planted new merchants found (the other 2 fall in the first 30 days), with 2 false flags (first-time concert tickets).
- **Existing data:** `python -m scripts.refresh_insights` (from `backend/`) runs detection for every user. It's idempotent, and the Docker backend runs it on every start, after migrations.

### Budgets

`/budgets` sets a monthly limit per spending category and tracks spending against it (`app/routers/budgets.py`, `app/services/budgets.py`; design: [`docs/specs/budgets.md`](docs/specs/budgets.md)).

- **One limit per category per month.** Months don't carry over; **Fill blanks from last month** copies last month's limits into categories with none, and editing one month never changes another.
- **The page** is a dial banner for the month, then a spreadsheet of all 11 spending categories (type in the Budget cell; Enter or leaving the cell saves, Esc undoes, clearing removes with undo). Clicking a category opens its 8 largest counted purchases and the change from last month.
- **Spent uses the same spending definition as verified answers** (`app/agent/spending.py`), so the page and a chat answer for the same month can't disagree. Transactions waiting in the review queue aren't counted, and the page says how many there are.
- **Status:** `over` when spent is above the limit, `near` from 80% of it (`BUDGET_NEAR_FRACTION`), else `ok`.
- **Chat doesn't answer budget questions yet**; they get the fixed refusal.

## Verified answers (`POST /questions`)

The LLM never writes a number. It only turns the question into a query plan (period + categories). Code checks the plan against the question, re-runs SQL scoped to the logged-in user, renders a fixed sentence, and verifies every digit against the stored results before answering. Design: `spec2.md` (proposal) and [`docs/specs/spec2-implementation.md`](docs/specs/spec2-implementation.md) (build guide).

- **Always 201.** A `queries` row is created for every question, so the outcome is in `status`, not the HTTP code: `verified`, `unverified` (the written answer failed verification; `answer` is null and `final_results` holds the raw rows) or `failed` (`answer` is a fixed apology). Fixed replies (greeting, unsupported question, future period) are `verified` with `plan.intent = "no_data"`. Only 401, 422 and 429 (more than 20 questions a minute) are errors.
- **`extra`** is an optional LLM sentence that is *not* verified (it may not contain digits, number words or comparisons). Show it labeled as such.
- **`failure_reason` is never returned**, because for `unverified` it contains the rejected text. It's kept in the database for audit.
- **Scope:** two intents, "how much did I spend on X in period Y" (`category_total`, from `group_by_category`) and "how many purchases / how many times did I … in period Y" (`transaction_count`, from `get_transactions`). A second tool, `list_plan`, answers "what recurring payments / subscriptions do I have?" (`recurring_list`) and "any duplicate or unusual charges?" (`flag_list`) from the stored detection results, with no period. List answers never get the `extra` sentence, because they contain merchant names and that call would send them to the LLM. "Subscriptions" with a period ("in September") is still the Subscriptions category total. Merchant questions, comparisons, income, averages, "why" questions, future periods and ranges over a year get a fixed "I can't answer that yet" reply. Missing data is never shown as $0.00: a period with no imported transactions gets a "no data" sentence, and partial coverage gets a note.
- **LLM setup:** set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` in `.env` (an OpenAI-compatible `/chat/completions` API), then run `python -m scripts.check_llm` from `backend/` once. It prints the `LLM_TOOL_CHOICE` / `LLM_TEMPERATURE` values to use. Without these settings the app still starts, and every question ends as `failed`. CI never calls the LLM. Each planning call is capped at `LLM_CALL_TIMEOUT_S` (11 s); a timed-out or 5xx call is retried once if 6 s of the 20 s budget remain.

With the LLM configured, imports are categorized automatically (see [Categorization](#categorization)). For a demo whose numbers match `fixtures/README.md` exactly, seed a user with the fixture labels applied:

```
cd backend
python -m scripts.seed_demo_user            # DEMO_EMAIL / DEMO_PASSWORD from .env (or --email / --password)
python -m scripts.seed_demo_user --reset    # delete the demo user and everything they own first
```

It imports `fixtures/sample_transactions.csv` through the API (in-process), applies `fixtures/sample_labels.csv`, adds the demo extras, and prints the monthly totals. It refuses to run against the test database. Without `--reset`, re-running only puts the fixture rows back to their labels; anything added since stays.

**Demo extras** (`app/devdata.py`), so the demo account shows the review queue and saved rules: three saved rules (STARBUCKS → Dining, UMD DINING → Dining, and AMAZON → Shopping, a mixed merchant), and four October transactions waiting in review (two Chipotle guesses at 55% and 60%, a thrift-store guess at 48%, and an uncategorized bookstore charge). **They change no total in `fixtures/README.md`**: the rules only re-mark rows already in their category, and transactions in review aren't counted. Answers about October do say "4 transactions aren't categorized yet". Tests and the evaluation don't use the seed script, so they're unaffected.

### Known limitations of the spending definition

Spending for a category is −(sum of signed amounts), over the 11 categories that aren't Income or Transfers; a refund lowers spending.

- **The sign convention comes from the CSV.** Some credit-card exports list purchases as positive amounts, which makes spending read as refunds. The import doesn't know a bank's convention.
- **Multiple accounts.** A card payment from checking is excluded only if it's categorized as Transfers. Until transfer pairing, a mislabeled card payment counts twice.
- **Venmo/Zelle payments to friends are Transfers**, so they aren't spending even when they pay for a shared meal.
- **A purchase count is money-out rows only**, so a refund isn't a purchase. "All spending" counts only the 11 spending categories, never payroll or transfers. Uncategorized transactions aren't counted, and unlike spending totals, the count answer doesn't say how many were left out.
- **One category per transaction.** A purchase that mixes categories (one Walmart trip with groceries and clothes) counts entirely in the category you pick. Split transactions aren't supported.
- **A saved correction applies to every future import from that merchant**, including multi-purpose ones. The review queue doesn't save one by default for mixed merchants, but the Transactions page's "apply to others" prompt can. Wrong ones can be changed or removed under Saved rules on the Review page, but nothing warns you that a rule is wrong.
- **Undo is limited.** Changing one transaction on the Transactions page shows "Was Dining · Undo", which puts the category back (as one you set, not the AI's original guess). Changing or removing a saved rule asks first but can't be undone afterwards, and resolving a merchant on the Review page has no undo; a remembered mistake is fixed under Saved rules.
- **Recurring payments are monthly only.** Weekly and yearly charges aren't detected. Two same-price charges 30 days apart from a merchant with nothing else in between count as a series (the cost of a 2-charge minimum).
- **"Stopped" is relative to your latest import across all accounts**, so if one account is imported through October and another only through July, the July account's recurring payments look stopped. Only the most recent price change is shown.
- **Flag thresholds are tuned to our generated data.** A first-time purchase over $75 (a concert ticket) is flagged as a new merchant; dismiss it. Merchant cleanup keeps location suffixes, so ATM withdrawals in different towns count as different merchants.
- **Chat can't answer about one merchant** ("When is Netflix due?"): questions naming a merchant are still refused. List answers describe the present only, so "What subscriptions did I have in September?" gets the fixed reply.
- **Transactions waiting in review aren't counted in answers or budgets** until you confirm them, so totals for a month with many unreviewed rows run low. Answers say how many were left out, and the Budgets page shows the count. The Transactions page still lists a low-confidence guess under its guessed category (so filtering by Dining shows it), but marks it with a dashed chip, "Guess · 55% · waiting in review, not counted yet" and a Confirm button, and its "Need a category" tile counts guesses as well as uncategorized rows, matching the Review page.
- **Budgets are per category only**, with no rollover of unspent money, and chat can't answer about them yet.

## Question UI (`frontend/`)

React + TypeScript (Vite). Start the backend first (`uvicorn app.main:app --reload`, port 8000), then:

```
cd frontend
npm ci
npm run dev          # http://localhost:5173 — /api is proxied to the backend, so no CORS is needed
```

`npm test` (Vitest), `npm run typecheck`, `npm run lint` and `npm run build` are what CI runs. The component tests use `src/test/fixtures/answers.json`, which is generated from the backend's template cases. If a backend change alters an answer's text, slots or result shape, `tests/agent/test_ui_contract.py` fails until you regenerate it from `backend/` with `python -m tests.agent.write_ui_fixtures`.

## Schema changes

Edit `backend/app/models.py`, then from `backend/`:

```
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Read the generated file before committing it. On MySQL, remove any `op.drop_index` lines in `downgrade()` that come right before an `op.drop_table` for the same table.

## Test data

- `fixtures/`: a small committed CSV with an answer key and expected totals. See [fixtures/README.md](fixtures/README.md).
- `python3 scripts/generate_data.py`: a larger synthetic dataset (6 months, ~530 rows) written to `data/`. Run with `--help` for options.

## Evaluation

Three scripts, run from `backend/`. Each prints a summary and writes a JSON report to `backend/eval/results/`. The two LLM evaluations can `--record` the real replies to a file and later `--replay` them, so a run can be repeated without calling the API. Re-record after any prompt or schema change. See [backend/eval/README.md](backend/eval/README.md).

| Metric (proposal target) | Command |
|---|---|
| Categorization accuracy on 529 labeled rows (≥ 80%) | `python3 ../scripts/generate_data.py` once, then `python -m scripts.eval_categorization --record eval/recordings/categorization.json` |
| Answers shown with correct numbers, 30 questions (≥ 95%) | `python -m scripts.eval_answers --record eval/recordings/answers.json` (needs the MySQL container; uses its own `pocketwatch_eval` database) |
| Verifier catch rate on generated mutations (≥ 90%) | `python -m scripts.eval_verifier` (no LLM or database; also enforced in CI by `tests/agent/test_mutations.py`) |
