# Evaluation

| Path | What it is |
|---|---|
| `questions.json` | The 30 evaluation questions with their expected plans (or fixed-reply labels). Asked as the fixture user on 2026-11-05. |
| `results/` | JSON reports written by the scripts. Commit the run you report. |
| `recordings/` | Recorded LLM replies (`--record`), replayable with `--replay` without an API key. |

All commands run from `backend/`.

## Categorization (`scripts/eval_categorization.py`)

Runs the import-time pipeline on `data/transactions.csv` (529 rows, from `python3 ../scripts/generate_data.py`) with no saved corrections, then compares the result with `data/labels.csv`. It reports:

- **Top-guess accuracy:** the proposal's metric (≥ 80%). Rows the LLM didn't answer count as wrong.
- **At the threshold:** how many rows were categorized automatically and how many of those were right, plus how many went to review and how many of those would have been wrong. This is the trade-off `REVIEW_CONFIDENCE_THRESHOLD` controls.
- Accuracy per category, accuracy per stated-confidence bucket (is the model's confidence worth trusting?), a threshold sweep, and the most common mistakes with the merchants behind them.
- The number of LLM calls and total time, to compare with the 45 s import budget.

The time limit is lifted for the run, so the accuracy covers every row.

Each recorded reply stores a fingerprint of the request it answered, so replaying after a prompt, batching or merchant-cleanup change stops with "Recording is stale" instead of scoring replies against the wrong batches.

## Answers (`scripts/eval_answers.py`)

Rebuilds `pocketwatch_eval` (override with `EVAL_DATABASE_URL`), imports `fixtures/sample_transactions.csv` for one user, applies the answer key, and asks each question through `answer_question`, the same code `POST /questions` runs. Each answer is scored two ways:

- **Plan:** intent, resolved dates and category set match `expect`, or the fixed reply has an accepted label.
- **Numbers:** every amount or count the answer should contain appears in the text. The expected values are computed by the script from `fixtures/sample_labels.csv`, separately from the app's SQL.

Headline: **shown answers with correct numbers**, out of the answers the user would see as verified with numbers (target ≥ 95%). The report also gives plan accuracy, the verified rate, unverified and failed counts, how many plans were rejected at least once, and latency.

`--categories llm` categorizes the fixture with the LLM pipeline instead of the answer key, so numbers are checked end to end as a real user would get them.

`tests/agent/test_eval_questions.py` feeds each expected plan to the real loop and checks that the harness scores it correct. A wrong expectation is caught in CI, before a live run.

## Verifier (`scripts/eval_verifier.py`)

Generates mutations of every known-good answer in `tests/agent/cases.py`:

- every digit changed to every other digit
- money amounts swapped
- a sign added
- a comma or the decimal point removed
- words changed to near-synonyms that change the meaning
- categories and months swapped
- lines dropped
- numbers or number words put in the extra sentence

It reports the share caught, per kind of mutation, and lists the survivors. The digit mutations make up most of the set, so report the per-kind rates too. Each survivor is either a verifier gap or a mutation that doesn't change the meaning, and the write-up has to say which.
