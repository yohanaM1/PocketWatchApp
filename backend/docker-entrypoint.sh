#!/bin/sh
# Container start: make sure there's a JWT secret, apply migrations, then run the API.
set -e

# A clean clone has no .env. Rather than refuse to start, generate a random secret into
# $JWT_SECRET_FILE (set in the Dockerfile), which app/config.py reads when JWT_SECRET is empty.
# A file, not an exported variable, so `docker compose exec` commands see it too. It lives in the
# container, so logins survive a restart but reset when the container is recreated (`down`/`up`).
# A secret that is set but weak (e.g. change-me) still fails in app/config.py.
if [ -z "${JWT_SECRET:-}" ]; then
    if [ ! -s "$JWT_SECRET_FILE" ]; then
        (umask 077 && python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$JWT_SECRET_FILE")
    fi
    echo "WARNING: JWT_SECRET is not set; using a generated one. Logins reset when the container is recreated. Set JWT_SECRET in .env for a real deployment." >&2
fi

# Idempotent: does nothing when the schema is already current.
alembic upgrade head

# Recurring series and flags for data imported before detection existed. Idempotent too. A failure is
# logged but doesn't stop the API: results also update on every import and category change.
python -m scripts.refresh_insights || echo "WARNING: refresh_insights failed; see the messages above." >&2

# One worker: the per-user question rate limit lives in process memory.
# The backend port is only reachable through nginx, so trusting its forwarded headers is safe.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
