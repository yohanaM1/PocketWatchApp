"""Answer accuracy on the 30 evaluation questions (proposal target: >= 95% of shown answers have correct numbers).

Run from backend/ with the docker-compose MySQL up:

    python -m scripts.eval_answers --record eval/recordings/answers.json    # real LLM, saves its replies
    python -m scripts.eval_answers --replay eval/recordings/answers.json    # no API calls
    python -m scripts.eval_answers --record ... --categories llm            # categorize with the pipeline, not the key

Rebuilds a separate database (EVAL_DATABASE_URL, default pocketwatch_eval) on every run, imports
fixtures/sample_transactions.csv for one user, applies the answer key (or, with --categories llm,
runs the categorization pipeline instead), then asks every question in eval/questions.json through
answer_question, the same code POST /questions runs.

Each answer is checked two ways:
- plan: intent, resolved period and category set match the expected plan (or the fixed-reply label);
- numbers: every expected amount or count, computed here from fixtures/sample_labels.csv and
  nothing else, appears in the answer text.
Writes eval/results/answers.json and prints a summary.
"""

import argparse
import calendar
import csv
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, insert, make_url, select, text
from sqlalchemy.orm import Session

from app import config
from app.agent.loop import answer_question
from app.agent.spending import SPENDING_CATEGORIES, counted_category_id
from app.categories import DEFAULT_CATEGORIES
from app.devdata import apply_labels
from app.models import Account, AccountType, Base, Category, Transaction, User
from app.models import VerificationStatus as VS
from app.services.categorization import categorize_with_llm
from app.services.csv_import import import_hashes, parse_csv
from app.services.merchant import normalize_merchant
from scripts.eval_common import EVAL_DIR, REPO, latency_summary, make_llm, pct

FIXTURE_CSV = REPO / "fixtures" / "sample_transactions.csv"
LABELS_CSV = REPO / "fixtures" / "sample_labels.csv"
DEFAULT_DB = "mysql+pymysql://root:rootpass@127.0.0.1:3306/pocketwatch_eval"


def fresh_db(url: str) -> Session:
    """Drop and recreate every table in the eval database, seed the categories, return a session."""
    u = make_url(url)
    if u.database in ("pocketwatch", "pocketwatch_test"):
        raise SystemExit(f"Refusing to rebuild {u.database}; point EVAL_DATABASE_URL at a database of its own.")
    server = create_engine(u.set(database=""))
    with server.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{u.database}`"))
    server.dispose()
    engine = create_engine(u)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(Category), [{"name": n} for n in DEFAULT_CATEGORIES])
    return Session(engine, expire_on_commit=False)


def seed_user(db: Session, categories: str, llm) -> tuple[int, dict]:
    """One user with the fixture imported into Checking and categorized; (user id, categorization counts)."""
    user = User(email="eval@example.com", password_hash="!login-disabled")
    db.add(user)
    db.flush()
    account = Account(user_id=user.id, name="Checking", type=AccountType.CHECKING)
    db.add(account)
    db.flush()
    raw = FIXTURE_CSV.read_bytes()
    rows = parse_csv(raw)
    txns = [Transaction(account_id=account.id, date=r.date, amount=r.amount, raw_description=r.description,
                        merchant=normalize_merchant(r.description), import_hash=h)
            for r, h in zip(rows, import_hashes(rows))]
    db.add_all(txns)
    db.flush()
    counts = {}
    if categories == "key":
        apply_labels(db, account.id, raw, LABELS_CSV.read_text())
    else:
        counts = categorize_with_llm(db, [t.id for t in txns], llm.use("categorize"))
    db.commit()
    return user.id, counts


def resolve_expected(period, today: date) -> tuple[date, date]:
    """Start and end dates of an expected period; computed here, not with app.agent.periods."""
    if isinstance(period, dict):
        return date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    y, m = map(int, period.split("-"))
    end = date(y, m, calendar.monthrange(y, m)[1])
    return date(y, m, 1), min(end, today)


def money(v: Decimal) -> str:
    """How the answer shows an amount: $1,234.56 (refunds are shown as a positive amount too)."""
    return f"${abs(v):,.2f}"


def expected_strings(expect: dict, labels: list[dict], today: date) -> list[str]:
    """The fragments a correct answer must contain, from the answer key."""
    start, end = resolve_expected(expect["period"], today)
    in_period = [r for r in labels if start <= date.fromisoformat(r["date"]) <= end]
    if not in_period:
        return ["I don't have any imported transactions"]
    cats = list(SPENDING_CATEGORIES) if expect["categories"] == "all_spending" else expect["categories"]
    picked = [r for r in in_period if r["category"] in cats]
    if expect["intent"] == "transaction_count":
        n = sum(1 for r in picked if Decimal(r["amount"]) < 0)
        return [f"You made {n} purchase{'s' if n != 1 else ''}"]
    spend = {c: -sum((Decimal(r["amount"]) for r in picked if r["category"] == c), Decimal("0")) for c in cats}
    out = [money(sum(spend.values(), Decimal("0")))]
    if isinstance(expect["categories"], list) and len(cats) > 1:
        out += [f"{money(v)} on {c}" if v >= 0 else f"Refunds in {c} exceeded spending by {money(v)}"
                for c, v in spend.items()]
    return out


def assigned_categories(db: Session, labels: list[dict]) -> list[tuple[date, str, str | None]]:
    """(date, true category, category answers count it under, or None while it awaits review) per fixture row."""
    truth = {(r["date"], r["description"], r["amount"]): r["category"] for r in labels}
    rows = db.execute(select(Transaction.date, Transaction.raw_description, Transaction.amount, Category.name)
                      .outerjoin(Category, Category.id == counted_category_id())).all()
    return [(d, truth[(d.isoformat(), desc, f"{amt:.2f}")], cat) for d, desc, amt, cat in rows]


def miscategorized(expect: dict, assigned, today: date) -> bool:
    """True if a row in the period was given a category that moves it into or out of the asked-for set."""
    if "fixed" in expect:
        return False
    start, end = resolve_expected(expect["period"], today)
    cats = set(SPENDING_CATEGORIES) if expect["categories"] == "all_spending" else set(expect["categories"])
    return any(start <= d <= end and got is not None and (got in cats) != (true in cats) for d, true, got in assigned)


def plan_matches(expect: dict, plan: dict | None, today: date) -> tuple[bool, str]:
    """(ok, why not) comparing the stored plan with the expected one."""
    if plan is None:
        return False, "no plan (failed)"
    if "fixed" in expect:
        if plan.get("intent") == "no_data" and plan.get("label") in expect["fixed"]:
            return True, ""
        return False, f"expected fixed {expect['fixed']}, got {plan.get('intent')}/{plan.get('label')}"
    if plan.get("intent") == "no_data":
        return False, f"expected an answer, got fixed {plan.get('label')}"
    start, end = resolve_expected(expect["period"], today)
    got_cats = set(SPENDING_CATEGORIES) if plan["categories"] == "all_spending" else set(plan["categories"])
    want_cats = set(SPENDING_CATEGORIES) if expect["categories"] == "all_spending" else set(expect["categories"])
    problems = []
    if plan["intent"] != expect["intent"]:
        problems.append(f"intent {plan['intent']}")
    if (plan["resolved"]["start"], plan["resolved"]["end"]) != (start.isoformat(), end.isoformat()):
        problems.append(f"period {plan['resolved']['start']}..{plan['resolved']['end']}")
    if got_cats != want_cats:
        problems.append(f"categories {sorted(got_cats)}")
    return not problems, "; ".join(problems)


def main() -> int:
    """Seed, ask every question, score, print and save."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--record", type=Path, help="call the real LLM and save its replies here")
    src.add_argument("--replay", type=Path, help="replay saved replies instead of calling the LLM")
    ap.add_argument("--categories", choices=["key", "llm"], default="key",
                    help="categorize the fixture with the answer key (default) or with the LLM pipeline")
    ap.add_argument("--questions", type=Path, default=EVAL_DIR / "questions.json")
    ap.add_argument("--out", type=Path, default=None, help="default eval/results/answers[-llm-categories].json")
    args = ap.parse_args()
    out_path = args.out or EVAL_DIR / "results" / f"answers{'-llm-categories' if args.categories == 'llm' else ''}.json"

    spec = json.loads(args.questions.read_text())
    today = date.fromisoformat(spec["today"])
    labels = list(csv.DictReader(LABELS_CSV.open()))
    llm = make_llm(args.record, args.replay)
    db = fresh_db(os.environ.get("EVAL_DATABASE_URL", DEFAULT_DB))
    user_id, cat_counts = seed_user(db, args.categories, llm)
    assigned = assigned_categories(db, labels)

    results = []
    for q in spec["questions"]:
        out = answer_question(db, user_id, q["question"], llm.use(q["id"]), today)
        expect = q["expect"]
        plan_ok, plan_why = plan_matches(expect, out.plan, today)
        shown_numeric = out.status is VS.VERIFIED and (out.plan or {}).get("intent") != "no_data"
        want = [] if "fixed" in expect else expected_strings(expect, labels, today)
        missing = [w for w in want if w not in (out.answer or "")]
        numbers_ok = shown_numeric and not missing if want else None
        # Lower than the key only because rows await review, and the answer says so: incomplete, not wrong.
        disclosed = bool(shown_numeric and missing and "categorized yet" in (out.answer or "")
                         and not miscategorized(expect, assigned, today))
        results.append({
            "id": q["id"], "question": q["question"], "status": out.status.value, "attempts": out.attempts,
            "latency_ms": out.latency_ms, "plan_ok": plan_ok, "plan_problem": plan_why or None,
            "shown_numeric": shown_numeric, "numbers_ok": numbers_ok,
            "incomplete_disclosed": disclosed, "missing": missing or None,
            "answer": out.answer, "extra": out.extra, "plan": out.plan, "failure_reason": out.failure_reason,
        })
        mark = "ok " if plan_ok and numbers_ok is not False else "BAD"
        print(f"{mark} {q['id']} [{out.status.value}] {q['question'][:60]}"
              + (f"  -- plan: {plan_why}" if not plan_ok else "")
              + (f"  -- missing {missing}" if missing and shown_numeric else "")
              + (" (uncategorized rows disclosed)" if disclosed else ""))
    if args.record:
        llm.save(args.record)

    n = len(results)
    numeric_qs = [r for r in results if "fixed" not in next(q for q in spec["questions"] if q["id"] == r["id"])["expect"]]
    shown = [r for r in results if r["shown_numeric"]]
    shown_right = [r for r in shown if r["numbers_ok"]]
    shown_disclosed = [r for r in shown if r["incomplete_disclosed"]]
    shown_wrong = [r for r in shown if not r["numbers_ok"] and not r["incomplete_disclosed"]]
    rejected = [r for r in results if r["attempts"] == 2 or (r["failure_reason"] or "").startswith("plan rejected")]
    summary = {
        "plan_accuracy": {"correct": sum(r["plan_ok"] for r in results), "questions": n},
        "shown_answers_with_correct_numbers": {"correct": len(shown_right), "shown": len(shown)},
        "shown_answers_incomplete_but_disclosed": len(shown_disclosed),
        "shown_answers_wrong_without_disclosure": [r["id"] for r in shown_wrong],
        "numeric_questions_answered_correctly": {"correct": sum(1 for r in numeric_qs if r["numbers_ok"]),
                                                 "questions": len(numeric_qs)},
        "verified_rate_numeric": {"verified": sum(1 for r in numeric_qs if r["status"] == "verified"),
                                  "questions": len(numeric_qs)},
        "unverified": sum(1 for r in results if r["status"] == "unverified"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "plan_rejections": len(rejected),
        "latency": latency_summary([r["latency_ms"] for r in results if r["latency_ms"] is not None]),
    }
    report = {"run_at": datetime.now().isoformat(timespec="seconds"), "source": "replay" if args.replay else "live",
              "model": config.LLM_MODEL or None, "prompt_version": config.PROMPT_VERSION, "today": spec["today"],
              "categories": args.categories, "categorization": cat_counts or None, "summary": summary,
              "results": results}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")

    s = summary
    print(f"\nPlan accuracy: {pct(s['plan_accuracy']['correct'], n)}")
    print(f"Shown answers with correct numbers (target >= 95%): "
          f"{pct(len(shown_right), len(shown))}")
    if shown_disclosed or shown_wrong:
        print(f"  of the rest: {len(shown_disclosed)} short because rows await review (said so in the answer), "
              f"{len(shown_wrong)} wrong without saying so {[r['id'] for r in shown_wrong] or ''}")
    print(f"Numeric questions answered correctly: {pct(s['numeric_questions_answered_correctly']['correct'], len(numeric_qs))}")
    print(f"Verified rate on numeric questions: {pct(s['verified_rate_numeric']['verified'], len(numeric_qs))}")
    print(f"Unverified: {s['unverified']}   failed: {s['failed']}   plan rejected at least once: {s['plan_rejections']}")
    if s["latency"]:
        print(f"Latency: p50 {s['latency']['p50_ms']} ms, p95 {s['latency']['p95_ms']} ms")
    if cat_counts:
        print(f"Fixture categorized by the pipeline: {cat_counts}")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
