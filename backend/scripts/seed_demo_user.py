"""Create a demo user with the labeled fixture data, through the real API (in-process).

Run from backend/:  python -m scripts.seed_demo_user [--reset] [--email demo@example.com] [--password <8+ chars>]

The email and password default to DEMO_EMAIL / DEMO_PASSWORD from .env, so the demo login is written
down in a file that is never committed. --email / --password override them.

Runs the app in-process with TestClient, so the import goes through the unchanged import router and
the labeling step uses the same DATABASE_URL. Refuses to touch a test database.

After labeling it adds the demo extras (app/devdata.py): three saved rules and four October transactions waiting
in review, so the Review page and Saved rules have something to show. They change no total in fixtures/README.md.

Re-running without --reset only puts the fixture rows back to their labels; anything added since (imports, rules,
budgets, questions) stays. --reset deletes the demo user and everything they own first, for a clean demo account.
"""

import argparse
import os
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from sqlalchemy import make_url

from app.db import DATABASE_URL, SessionLocal

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


# Command-line flags, falling back to DEMO_EMAIL / DEMO_PASSWORD
def parse_credentials(argv: list[str] | None = None, env: Mapping[str, str] = os.environ) -> argparse.Namespace:
    """The demo email and password: flags first, then DEMO_EMAIL / DEMO_PASSWORD, then demo@example.com for the email.

    Exits with a usage error when no password is given anywhere or it's shorter than 8 characters.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", default=env.get("DEMO_EMAIL") or "demo@example.com")
    ap.add_argument("--password", default=env.get("DEMO_PASSWORD") or None)
    ap.add_argument("--reset", action="store_true",
                    help="delete the demo user and all their data first, then seed from scratch")
    args = ap.parse_args(argv)
    if not args.password:
        ap.error("no password: set DEMO_PASSWORD in .env, or pass --password")
    if len(args.password) < 8:
        ap.error("the password must be at least 8 characters")
    return args


def main() -> int:
    """Register (or reuse) the demo user, import and label the fixture, print the monthly totals."""
    args = parse_credentials()

    db_name = make_url(DATABASE_URL).database or ""
    test_url = os.environ.get("TEST_DATABASE_URL")
    if db_name.endswith("_test") or (test_url and make_url(test_url).database == db_name):
        print(f"Refusing to seed the test database {db_name!r}", file=sys.stderr)
        return 1

    from fastapi.testclient import TestClient

    from app.agent.periods import now_et
    from app.agent.plan import GroupByCategoryArgs
    from app.agent.tools.spending import group_by_category
    from app.devdata import add_demo_extras, apply_labels, delete_user
    from app.main import app

    if args.reset:
        with SessionLocal() as db:
            existed = delete_user(db, args.email)
            db.commit()
        print(f"reset: {'deleted' if existed else 'no existing user'} {args.email}")

    csv_bytes = (FIXTURES / "sample_transactions.csv").read_bytes()
    labels = (FIXTURES / "sample_labels.csv").read_text()
    with TestClient(app) as client:
        r = client.post("/auth/register", json={"email": args.email, "password": args.password})
        if r.status_code not in (201, 409):
            print(f"register failed: {r.status_code} {r.text}", file=sys.stderr)
            return 1
        r = client.post("/auth/login", data={"username": args.email, "password": args.password})
        if r.status_code != 200:
            print("login failed (wrong password for an existing user? check DEMO_PASSWORD / --password)", file=sys.stderr)
            return 1
        headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        accounts = client.get("/accounts", headers=headers).json()
        checking = next(a for a in accounts if a["name"] == "Checking")  # created by registration
        r = client.post(f"/accounts/{checking['id']}/imports", headers=headers,
                        files={"file": ("sample_transactions.csv", csv_bytes, "text/csv")})
        r.raise_for_status()
        print(f"import: {r.json()}")
        user_id = client.get("/auth/me", headers=headers).json()["id"]

    with SessionLocal() as db:
        n = apply_labels(db, checking["id"], csv_bytes, labels)
        rules, review_rows = add_demo_extras(db, checking["id"])
        db.commit()
        print(f"labeled {n} transactions for {args.email} (account {checking['id']})")
        print(f"demo extras: {rules} saved rules, {review_rows} new transactions waiting in review")
        for title, today in [("full months, compare to fixtures/README.md", date(2026, 11, 5)),
                             (f"as of today ({now_et().date()}, ET)", now_et().date())]:
            print(f"\nSpending totals, {title}:")
            for month in ("2026-09", "2026-10"):
                try:
                    res = group_by_category(db, user_id, GroupByCategoryArgs(period=month), today)
                except ValueError as e:
                    print(f"  {month}: {e}")
                    continue
                cats = ", ".join(f"{r['category']} {r['net_amount']}" for r in res["rows"] if r["count"])
                print(f"  {month} ({res['period']['start']}..{res['period']['end']}): "
                      f"total {res['total']['net_amount']}; {cats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
