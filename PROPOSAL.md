# PocketWatch — Project Proposal

## Project Name

PocketWatch

## Team Members

Team of 2:

- Yohana Michael (`yohana23`)
- Peniel Johnson (`pjohns10`)

## Problem Statement

Working with their finances can be a hassle for most college students. Whether it's forgetting to check their transactions or not understanding how they're spending so much money, many of them struggle to keep up with their spending and budget. We want to provide an app that gives them a more complete way to understand their finances.

## Target Users

College students who want to track their finances and keep a closer eye on their spending.

## Pitch

PocketWatch helps college students understand where their money goes by organizing their transactions automatically and answering questions about their spending in plain English, with every number checked against their real data.

## Why AI Is Necessary or Useful Here

AI is necessary in our implementation because we want users to be able to ask open-ended questions such as, "Why did my spending increase this month?" The AI can retrieve previous months and the current month and point out expensive transactions that occurred during that period. Although the data comes from transactions, AI is needed to explain the answer to users in plain language.

## Main Features

Users can:

1. Import transactions from a CSV file.
2. See their transactions automatically categorized, and correct any category.
3. Review transactions the system was unsure how to categorize.
4. See their recurring payments and subscriptions.
5. See flagged unusual or duplicate transactions.
6. Set monthly budgets per category and track progress.
7. Ask questions about their spending in plain English and get answers with the supporting data shown.
8. View a history of their past questions and answers.

## AI Architecture

**Question answering.** When the user asks a question, the backend sends the question and all available tools to the LLM. The LLM chooses the tools it needs, and the server executes them using only the current user's data. The results are sent back to the LLM, which decides whether the answer is complete. A finished answer contains an explanation of the user's question and every number it used.

A verifier then checks that the numbers in the answer match the data returned by the tools. If verification fails, the AI is allowed to try again. If it still fails, the user receives a response saying the answer couldn't be verified, but can still see the underlying data.

**Categorization.** Imported transactions are first categorized using categories the system already knows the user has (including past corrections). Transactions it doesn't recognize are categorized in groups by the LLM. If the AI is not confident about a transaction, it is sent to the user for verification.

## Tools

| Tool | What it does |
| --- | --- |
| `get_transactions()` | Finds the user's transactions matching the given parameters. Meaningful because the transaction set grows as the user imports more files. |
| `group_by_category()` | Groups transactions into categories so the user can see where they are spending money. |
| `compare_periods()` | Computes the difference between spending behavior in one period and another. |
| `find_recurring()` | Finds transactions, such as subscriptions, that repeat regularly from the same merchant for the same amount. |
| `detect_anomalies()` | Detects unusual behavior in the transactions, such as outliers and duplicates. |

These tools are meaningful because each one queries or computes over the user's stored transaction data, rather than returning information that could simply be included in the prompt.

## Database Design

| Table | Columns | Notes |
| --- | --- | --- |
| `users` | `id`, `email`, `password_hash` | |
| `accounts` | `id`, `user_id` → `users`, `name`, `type` | |
| `transactions` | `id`, `account_id` → `accounts`, `date`, `amount`, `raw_description`, `merchant`, `category_id` → `categories`, `category_confidence`, `category_source` | |
| `categories` | `id`, `name` | |
| `category_corrections` | `id`, `user_id`, `merchant`, `category_id` | User fixes, reused for future imports |
| `recurring_series` | `id`, `user_id`, `merchant`, `typical_amount`, `interval_days` | |
| `anomaly_flags` | `id`, `transaction_id`, `reason`, `status` | |
| `budgets` | `id`, `user_id`, `category_id`, `month`, `limit_amount` | |
| `queries` | `id`, `user_id`, `question`, `tool_calls` (JSON), `answer`, `verification_status`, `created_at` | Audit log of every AI answer |

**Indexes:** `transactions(account_id, date)` and `transactions(category_id)`.

## Technology Stack

| Component | Choice | Why |
| --- | --- | --- |
| Frontend | React + TypeScript | We are both familiar with it and found it helpful in the last buzzer project. |
| Backend | FastAPI | We are familiar with using it to handle endpoints. |
| Database | MySQL 8 | We are familiar with MySQL, and MySQL 8 has the date-based queries we need for transactions. |
| ORM / migrations | SQLAlchemy + Alembic | Connects the database to the backend; we have both worked with these tools. |
| LLM | Course-provided API key | Neither of us has used it yet, but we will learn it in the course and research it until then. |
| Runtime | Docker + Docker Compose | Runs the whole app (with MySQL pinned to version 8) in the same environment on both of our machines. |

## Reliability Plan

Our system improves reliability through several layers of verification:

1. **Numeric verification.** Numeric claims are verified against tool outputs, so no number reaches the user unless it matches a verified result. This catches hallucinated figures, arithmetic errors, and numbers attributed to the wrong time period.
2. **Confidence-gated categorization.** Low-confidence categories are sent to a review queue instead of being silently guessed. User corrections are stored and reused to improve future results.
3. **Schema validation.** All LLM-generated output is schema-validated; malformed responses are rejected and retried automatically.

In the demo, we will show a question where an unverified model provides an incorrect total, followed by the verifier identifying the erroneous figure and preventing it from reaching the user.

## Evaluation Plan

| Area | Metric | Target |
| --- | --- | --- |
| Categorization | Accuracy on 500 labeled transactions | ≥ 80% |
| Recurring payments | Recall on planted subscriptions, with few false matches | ≥ 85% |
| Answer accuracy | Answers shown to users with correct numbers, across 30 test questions | ≥ 95% |
| Verification failure rate | How often answers fail verification (how often the AI gets numbers wrong on its own) | Tracked |
| Verifier effectiveness | Answers with intentionally incorrect numbers that the verifier catches | ≥ 90% |

## Security Considerations

- **Authentication:** users sign in with email and password; tokens are used for authentication.
- **Authorization:** users can only see their own data. The AI and its tools only have access to the logged-in user's data.
- **Prompt injection:** our threat model is an uploaded transaction file that hides malicious instructions (e.g., in a transaction description). Since we can't control the contents of uploaded files, file contents are treated strictly as data, and all numbers are verified against tool outputs.
- **Uploads:** uploaded files are checked for correct format and size.
- **No AI writes:** the AI is not allowed to modify user data.

## Risks

| Risk | Plan | Schedule impact | Adjustment |
| --- | --- | --- | --- |
| AI-generated numbers may be difficult to verify. | Use tool-verified values or templates. | Up to one week. | Simplify the verifier if needed. |
| Synthetic data may be too clean for meaningful evaluation. | Generate realistic, rule-based data. | 2–3 days. | Simplify a lower-priority feature if necessary. |
| LLM costs or rate limits may become a problem. | Cache responses and use recorded results in tests. | Minimal. | Reduce evaluation frequency if needed. |

## Teamwork

**Reducing friction:** we will use Docker Compose and a shared test-data script. This fits our team because we are both familiar with Docker Compose, and it lets us use the same environment even though our machines differ.

**Division of work:**

- **Yohana:** all views, authentication, authorization and per-user scoping, CSV import, budgets API, `find_recurring`, `detect_anomalies` and their agent integration, `compare_periods`, review queue and corrections storage, synthetic data generator, deployment, frontend tests, and tests for their own code.
- **Peniel:** question/answer view with the verification display, agent loop, LLM integration, `get_transactions`, `group_by_category`, verifier, LLM categorization pipeline, evaluation harness and test sets, AI evaluation, prompt injection attack and mitigation, CI pipeline, and tests for their own code.
- **Shared:** schema design, and reviewing each other's merge requests.

## Timeline

| Week | Yohana | Peniel |
| --- | --- | --- |
| Oct 5 | Schema (together); data generator v1, fixture CSV | Repo, Docker Compose, CI; test tool calling with the course key |
| Oct 12 | Auth; CSV import | `get_transactions`, `group_by_category`; start the agent loop |
| Oct 19 | Transactions view; `compare_periods` | Agent loop; question UI |
| Oct 26 | Deploy a skeleton version | **M2 (Oct 29):** import, DB, question, verified answer |
| Nov 2 | Categorization review queue and corrections | LLM categorization pipeline |
| Nov 9 | `find_recurring`, `detect_anomalies`, their views, and their agent integration (new answer intents, templates, verifier checks; see `docs/specs/recurring-and-anomalies.md`) | Evaluation harness + test sets; review the agent and verifier changes for recurring and anomalies |
| Nov 16 | Budgets; full deployment; saved rules list (view/delete corrections, see `docs/specs/review-queue.md`) | Run the evaluation (incl. calibrating the review-queue confidence threshold); prompt injection attack + fix; **M3 (Nov 19)** |
| Nov 23 | README sections | Record backup demo video; recurring recall evaluation (`eval_recurring`) and re-recording the answer evaluation for the new answer intents |
| Nov 30 | Frontend tests, polish, final deploy | Evaluation write-up, security write-up, report for Dec 1 |
| Dec 1 | Done | Done |
