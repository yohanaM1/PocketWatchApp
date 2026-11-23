# Backend image. Build from the repo root (docker-compose.yml does this) because
# scripts/seed_demo_user.py reads ../fixtures.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JWT_SECRET_FILE=/tmp/pocketwatch_jwt_secret

WORKDIR /app/backend

# Dependencies get their own layer, so a code change doesn't reinstall them.
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY fixtures /app/fixtures
COPY backend /app/backend

RUN chmod +x docker-entrypoint.sh \
    && useradd --create-home --uid 10001 app
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

ENTRYPOINT ["./docker-entrypoint.sh"]
