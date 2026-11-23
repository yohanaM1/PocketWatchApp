# Design spec — Full-stack `docker compose up` (Yohana, week of Nov 2)

## Context

The timeline row for Oct 26 is **Deploy a skeleton version**. Today is Nov 7, and **M3 is Nov 19**.

We don't know the host yet. A UMD VM is likely, but the course hasn't confirmed it. This spec covers only the part
that works on any host. COURSE_SPEC §6.11 requires it no matter where we deploy:

> `docker compose up` must start your frontend, backend, and database. [...] This is not optional.

Today [docker-compose.yml](../../docker-compose.yml) starts only MySQL. The backend runs with `uvicorn --reload` on the host,
and the frontend runs with `npm run dev`. Vite's dev proxy strips `/api`, but in production nothing does.

Once this lands, deploying to a VM is `git clone`, `.env`, then `docker compose up -d --build`. The hosting step is a separate task, done after the course answers.

**Not covered here:** choosing a host, HTTPS/domain, and CI building or deploying images.

Delivery: one branch/MR off `main`: `feat/docker-full-stack`.

---

## What runs

```
browser ──▶ web (nginx :80, published as ${WEB_PORT:-8080})
              ├─ /        → built React app (SPA fallback to index.html)
              └─ /api/*   → backend:8000/*  (prefix stripped, like vite.config.ts)
backend (uvicorn :8000, not published) ──▶ db (MySQL 8.4)
```

One origin, so there's no CORS and [client.ts](../../frontend/src/api/client.ts) stays unchanged.

---

## Part 1 — Backend image (`backend/Dockerfile`)

- **Build context is the repo root**, not `backend/`, because [seed_demo_user.py](../../backend/scripts/seed_demo_user.py)
  reads `../../fixtures` (`parents[2]`). The image copies `backend/` → `/app/backend` and `fixtures/` → `/app/fixtures`, with `WORKDIR /app/backend`.
- `python:3.12-slim`. `pip install -r requirements.txt` is a layer of its own, so code changes don't reinstall packages.
- Runs as a non-root user.
- `backend/docker-entrypoint.sh`:
  1. If `JWT_SECRET` is **unset or empty**, generate a random one into `$JWT_SECRET_FILE` (set in the Dockerfile; it's kept
     if it already exists) and print a warning. This keeps a clean clone working without committing a secret. A value that is set but
     weak (e.g. `change-me`) still fails loudly in [config.py](../../backend/app/config.py), as it does today.
     *Changed during implementation:* exporting the secret as a variable only reached uvicorn, so `docker compose exec ... seed_demo_user`
     crashed. Writing it to a file fixes that. `config.py` gains a small fallback that reads `JWT_SECRET_FILE` when
     `JWT_SECRET` is empty. As a bonus, logins survive `docker compose restart` and reset only when the container is recreated.
  2. `alembic upgrade head` (idempotent; the DB is already healthy thanks to `depends_on`).
  3. `exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers`, with **one worker**. The per-user question
     rate limit and the agent are in-process.
- Healthcheck: `python -c` that fetches the existing `http://127.0.0.1:8000/health` (slim has no curl).

## Part 2 — Frontend image (`frontend/Dockerfile` + `frontend/nginx.conf`)

- Multi-stage build: `node:20-alpine` runs `npm ci && npm run build`, then `nginx:1.27-alpine` serves `dist/`.
- `nginx.conf`:
  - `location /api/ { proxy_pass http://backend:8000/; }`: the trailing slash strips `/api`, matching the Vite rewrite.
    Forward `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`.
  - **`client_max_body_size 2m`.** The nginx default (1m) is below our 1,000,000-byte CSV limit plus multipart overhead.
    Without this, a large valid upload gets nginx's 413 HTML page instead of our JSON error.
  - `proxy_read_timeout 60s` (the agent's budget is 20s).
  - `location / { try_files $uri /index.html; }` so a refresh on `/transactions` works.
  - `index.html` is not cached, but hashed `/assets/*` files are cached long-term.

## Part 3 — `docker-compose.yml`

| Service | Change |
|---|---|
| `db` | Port becomes **`127.0.0.1:${MYSQL_PORT:-3306}:3306`**, so local `pytest`/`alembic` still reach it but a VM doesn't expose MySQL to the internet. Otherwise unchanged. |
| `backend` | `build: {context: ., dockerfile: backend/Dockerfile}`. `environment:` lists only the backend's own settings: `DATABASE_URL=mysql+pymysql://${MYSQL_USER}:${MYSQL_PASSWORD}@db:3306/${MYSQL_DATABASE}` (with the same defaults as `db`), plus `JWT_*`, `LLM_*` and `AGENT_EXPLORATION`, interpolated from `.env` with the same defaults as `config.py`. This is an allowlist, not `env_file`, so the MySQL root password, `TEST_DATABASE_URL` and the `127.0.0.1` dev URL never reach the container. `LLM_TEMPERATURE` uses `${LLM_TEMPERATURE-0}`, so an explicit empty value ("don't send it") is kept. `depends_on: db: service_healthy`. `restart: unless-stopped`. No published port. |
| `web` | `build: frontend`. `ports: ${WEB_PORT:-8080}:80`. `depends_on: backend: service_healthy` (nginx fails at startup if `backend` doesn't resolve). `restart: unless-stopped`. |

Local development doesn't change: `docker compose up -d db` plus host uvicorn/Vite still works.

## Part 4 — Supporting files

- **`.dockerignore`** (repo root, since the backend context is the root): `.git`, `.env`, `**/.venv`, `**/node_modules`,
  `frontend/dist`, `data/`, `**/__pycache__`, `.ruff_cache`, `**/.pytest_cache`. Its main job is keeping `.env` and `.venv` out of images.
- **`frontend/.dockerignore`**: `node_modules`, `dist`. The frontend context is `frontend/`, so the root file doesn't apply to it.
- **`.gitattributes`**: `*.sh text eol=lf`, so a Windows checkout can't break the entrypoint with CRLF line endings.
- **`.env.example`**: add `WEB_PORT=8080`. Note that `DATABASE_URL` is only for host runs, because compose sets its own.
- **README**: a new "Run everything with Docker" section above "Local setup":
  ```
  docker compose up --build            # http://localhost:8080
  docker compose exec backend python -m scripts.seed_demo_user --password <8+ chars>
  ```
  Plus: copy `.env` to set `JWT_SECRET` (required for a real deployment) and the `LLM_*` values (without them questions end as `failed`),
  and `docker compose down -v` resets the database.

No migrations. The only application code change is the `JWT_SECRET_FILE` fallback in `config.py` (see Part 1).

---

## Files

- New: `backend/Dockerfile`, `backend/docker-entrypoint.sh`, `frontend/Dockerfile`, `frontend/nginx.conf`, `.dockerignore`,
  `frontend/.dockerignore`, `.gitattributes`.
- Changed: `docker-compose.yml`, `.env.example`, `README.md`, `backend/app/config.py`.

## Verification

1. **Clean clone.** Clone the branch into a scratch directory with **no `.env`**, then run `docker compose -p pwclean up --build`
   with `WEB_PORT`/`MYSQL_PORT` set so it doesn't clash with the dev DB. All three services go healthy, and the backend logs the JWT warning.
2. In the browser at `http://localhost:<WEB_PORT>`:
   - register, which lands on `/ask`;
   - Transactions → upload `fixtures/sample_transactions.csv` → "Imported N";
   - refresh on `/transactions` (SPA fallback);
   - edit a category;
   - Ask a question (`failed` without LLM keys, which is expected; `verified` with them).
3. `docker compose exec backend python -m scripts.seed_demo_user --password ...` prints the monthly totals.
4. Upload a ~1 MB CSV and check that the error is our JSON, not nginx's 413 page.
5. `docker compose down` then `up` again: the data is still there, and the migrations don't re-run.
6. `docker compose down -v -p pwclean` to clean up. Existing `pytest` and frontend checks are unchanged and still pass.

## Later (separate tasks)

- Hosting: the VM (or a fallback cloud VM if there's no answer by ~Nov 12), a real `JWT_SECRET`, a test account, and HTTPS if the host allows.
- Optional CI job that runs `docker compose build`, if the course runners support Docker-in-Docker.
