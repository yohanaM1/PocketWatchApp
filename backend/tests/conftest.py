"""Test setup: a real MySQL database (pocketwatch_test), rolled back after every test.

Needs the docker-compose MySQL running. Override the URL with TEST_DATABASE_URL.
"""

import os

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert, make_url, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.agent.llm import _Unconfigured, get_llm  # noqa: E402
from app.categories import DEFAULT_CATEGORIES  # noqa: E402
from app.deps import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Category  # noqa: E402

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "mysql+pymysql://root:rootpass@127.0.0.1:3306/pocketwatch_test"
)


# Fixture: test database engine, built once per run
@pytest.fixture(scope="session")
def engine():
    """Create the test database once per test run: fresh tables plus the seeded categories."""
    url = make_url(TEST_DATABASE_URL)
    server = create_engine(url.set(database=""))
    with server.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{url.database}`"))
    server.dispose()

    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(Category), [{"name": n} for n in DEFAULT_CATEGORIES])
    yield engine
    engine.dispose()


# Fixture: per-test session that rolls back
@pytest.fixture
def db(engine):
    """A session whose commits only release a savepoint; everything is rolled back afterwards."""
    connection = engine.connect()
    outer = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield session
    session.close()
    outer.rollback()
    connection.close()


# Fixture: API client bound to the test session
@pytest.fixture
def client(db):
    """An API test client whose requests use the rolled-back test session.

    The LLM is the unconfigured stub, so a key in .env is never used by tests: imports skip the
    LLM categorization step. Tests that need an LLM install a FakeLLM over this.
    """
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_llm] = lambda: _Unconfigured()
    yield TestClient(app)
    app.dependency_overrides.clear()


# Helper: create a user and return auth headers
def register(client: TestClient, email: str, password: str = "correct-horse-1") -> dict[str, str]:
    """Register and log in; returns auth headers."""
    assert client.post("/auth/register", json={"email": email, "password": password}).status_code == 201
    token = client.post("/auth/login", data={"username": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
