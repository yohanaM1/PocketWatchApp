"""Categorization accuracy on the labeled synthetic data (proposal target: >= 80% on 500+ transactions).

Run from backend/ (no database needed):

    python3 ../scripts/generate_data.py                       # once: writes ../data (529 rows, 6 months)
    python -m scripts.eval_categorization --record eval/recordings/categorization.json   # real LLM
    python -m scripts.eval_categorization --replay eval/recordings/categorization.json   # no API calls

Runs the same code path as an import (merchant cleanup, grouping, batching, prompt, validation)
on every row, with no saved corrections, so it measures the LLM step alone. Writes a JSON report
to eval/results/categorization.json and prints a summary.
"""

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app import config
from app.services.csv_import import parse_csv
from app.services.llm_categorizer import build_groups, categorize_groups
from app.services.merchant import normalize_merchant
from scripts.eval_common import EVAL_DIR, REPO, make_llm, pct

THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
BUCKETS = [(0.0, 0.5), (0.5, 0.75), (0.75, 0.9), (0.9, 1.01)]


def estimated_wall_time(call_seconds: list[float], parallel: int) -> float:
    """How long the calls would take run `parallel` at a time, each starting when a worker frees up."""
    workers = [0.0] * max(parallel, 1)
    for t in call_seconds:
        i = workers.index(min(workers))
        workers[i] += t
    return max(workers) if call_seconds else 0.0


def main() -> int:
    """Categorize every row, compare with labels.csv, print and save the metrics."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=REPO / "data", help="folder with transactions.csv and labels.csv")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--record", type=Path, help="call the real LLM and save its replies here")
    src.add_argument("--replay", type=Path, help="replay saved replies instead of calling the LLM")
    ap.add_argument("--out", type=Path, default=EVAL_DIR / "results" / "categorization.json")
    args = ap.parse_args()

    if not (args.data / "transactions.csv").exists():
        print(f"No {args.data / 'transactions.csv'}. Run: python3 ../scripts/generate_data.py", file=sys.stderr)
        return 1
    parsed = parse_csv((args.data / "transactions.csv").read_bytes())
    labels = list(csv.DictReader((args.data / "labels.csv").open()))
    assert len(labels) == len(parsed), "labels.csv doesn't match transactions.csv"

    llm = make_llm(args.record, args.replay)
    rows = [(i, normalize_merchant(r.description), r.description, r.amount) for i, r in enumerate(parsed)]
    groups = build_groups(rows)
    import_budget, parallel = config.CATEGORIZE_TIME_LIMIT_S, config.CATEGORIZE_PARALLEL
    config.CATEGORIZE_TIME_LIMIT_S = 3600  # measure accuracy, not the import's time budget (timed below)
    config.CATEGORIZE_CALL_TIMEOUT_S = max(config.CATEGORIZE_CALL_TIMEOUT_S, 120)
    config.CATEGORIZE_PARALLEL = 1  # one call at a time, so a recording replays in order

    llm.use("all")  # one key: the whole run is one sequence of batch calls
    calls = [0]
    call_seconds: list[float] = []

    class Counting:
        def complete(self, *a, **kw):
            """Count and time the calls the pipeline makes."""
            calls[0] += 1
            t0 = time.monotonic()
            try:
                return llm.complete(*a, **kw)
            finally:
                call_seconds.append(time.monotonic() - t0)

    started = time.monotonic()
    guesses = categorize_groups(Counting(), groups)
    seconds = time.monotonic() - started
    if args.record:
        llm.save(args.record)
    est_import_s = estimated_wall_time(call_seconds, parallel)

    # Per-row prediction from its group's guess
    pred: dict[int, tuple[str, float] | None] = {}
    applies: dict[int, bool] = {}
    merchant_of: dict[int, str] = {}
    for g in groups:
        guess = guesses.get(g.key)
        for i in g.transaction_ids:
            pred[i] = (guess.category, guess.confidence) if guess else None
            applies[i] = bool(guess and guess.applies())
            merchant_of[i] = g.merchant
    truth = {i: labels[i]["category"] for i in range(len(labels))}
    n = len(truth)

    correct = sum(1 for i in truth if pred[i] and pred[i][0] == truth[i])
    unanswered = sum(1 for i in truth if pred[i] is None)
    t = config.REVIEW_CONFIDENCE_THRESHOLD
    auto = [i for i in truth if applies[i]]  # confident, and not overruled by code (llm_categorizer.checked)
    auto_right = sum(1 for i in auto if pred[i][0] == truth[i])
    auto_set = set(auto)
    review = [i for i in truth if i not in auto_set]
    review_wrong = sum(1 for i in review if not pred[i] or pred[i][0] != truth[i])

    per_cat = {}
    for cat in sorted(set(truth.values())):
        ids = [i for i in truth if truth[i] == cat]
        per_cat[cat] = {"rows": len(ids), "correct": sum(1 for i in ids if pred[i] and pred[i][0] == cat)}

    confusion = Counter((truth[i], pred[i][0]) for i in truth if pred[i] and pred[i][0] != truth[i])
    confusion_merchants = defaultdict(set)
    for i in truth:
        if pred[i] and pred[i][0] != truth[i]:
            confusion_merchants[(truth[i], pred[i][0])].add(merchant_of[i])

    buckets = []
    for lo, hi in BUCKETS:
        ids = [i for i in truth if pred[i] and lo <= pred[i][1] < hi]
        buckets.append({"confidence": f"{lo:.2f}–{min(hi, 1):.2f}", "rows": len(ids),
                        "correct": sum(1 for i in ids if pred[i][0] == truth[i])})

    sweep = []
    for th in THRESHOLDS:
        ids = [i for i in truth if pred[i] and pred[i][1] >= th]
        sweep.append({"threshold": th, "auto_rows": len(ids), "auto_correct": sum(1 for i in ids if pred[i][0] == truth[i])})

    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "source": "replay" if args.replay else "live", "model": config.LLM_MODEL or None,
        "data": str(args.data), "rows": n, "groups": len(groups), "llm_calls": calls[0],
        "seconds": round(seconds, 1), "call_seconds": [round(x, 1) for x in call_seconds],
        "estimated_import_seconds": round(est_import_s, 1), "parallel": parallel, "import_time_budget_s": import_budget, "batch_size": config.CATEGORIZE_BATCH_SIZE,
        "top_guess_accuracy": {"correct": correct, "rows": n},
        "unanswered_rows": unanswered,
        "threshold": t,
        "auto_categorized": {"rows": len(auto), "correct": auto_right},
        "sent_to_review": {"rows": len(review), "would_have_been_wrong": review_wrong},
        "per_category": per_cat,
        "confidence_buckets": buckets,
        "threshold_sweep": sweep,
        "confusions": [{"true": a, "predicted": b, "rows": k, "merchants": sorted(confusion_merchants[(a, b)])}
                       for (a, b), k in confusion.most_common()],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1) + "\n")

    print(f"{n} rows in {len(groups)} merchant groups, {calls[0]} LLM calls, {seconds:.1f}s "
          f"({'replayed' if args.replay else 'live'})")
    print(f"Top-guess accuracy (target >= 80%): {pct(correct, n)}   unanswered rows: {unanswered}")
    print(f"At threshold {t}: auto-categorized {pct(len(auto), n)}, of which correct {pct(auto_right, len(auto))}; "
          f"sent to review {len(review)} ({review_wrong} of them would have been wrong)")
    print("\nPer category:")
    for cat, v in per_cat.items():
        print(f"  {cat:<14} {pct(v['correct'], v['rows'])}")
    print("\nAccuracy by stated confidence:")
    for b in buckets:
        print(f"  {b['confidence']}: {pct(b['correct'], b['rows'])}")
    print("\nThreshold sweep (rows auto-categorized, precision):")
    for s in sweep:
        print(f"  {s['threshold']:.2f}: {s['auto_rows']:>4} rows, {pct(s['auto_correct'], s['auto_rows'])}")
    if confusion:
        print("\nMistakes (true -> predicted):")
        for c in report["confusions"][:10]:
            print(f"  {c['true']} -> {c['predicted']}: {c['rows']} rows ({', '.join(c['merchants'][:4])})")
    print(f"\nWrote {args.out}")
    if not args.replay:
        print(f"Calls took {', '.join(f'{x:.0f}s' for x in call_seconds)}; with {parallel} in parallel an import "
              f"would take about {est_import_s:.0f}s (budget {import_budget:.0f}s)")
        if est_import_s > import_budget:
            print("note: over the import budget; part of a real import this size would be left for review")
    return 0


if __name__ == "__main__":
    sys.exit(main())
