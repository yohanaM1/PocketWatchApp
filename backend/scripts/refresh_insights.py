"""Run recurring and anomaly detection for every user, so data imported before detection existed has results.

Run from backend/:  python -m scripts.refresh_insights

Idempotent: series are rebuilt with the same keys and existing flags are never changed, so running it again changes
nothing. docker-entrypoint.sh runs it after migrations. Each user is committed on their own, and one user's error
doesn't stop the others.
"""

import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.insights import refresh_insights


def main() -> int:
    """Refresh every user; exit 1 if any user failed."""
    failed = 0
    with SessionLocal() as db:
        user_ids = list(db.scalars(select(User.id).order_by(User.id)))
        for user_id in user_ids:
            try:
                counts = refresh_insights(db, user_id)
                db.commit()
            except Exception as e:  # keep going: one user's bad data shouldn't block the rest
                db.rollback()
                failed += 1
                print(f"user {user_id}: failed ({type(e).__name__}: {e})", file=sys.stderr)
                continue
            print(f"user {user_id}: {counts['series']} recurring series, {counts['new_flags']} new flags")
    print(f"refreshed {len(user_ids) - failed} of {len(user_ids)} users")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
