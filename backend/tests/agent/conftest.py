"""Agent test fixtures: a labeled fixture user, the rate limiter reset, and FakeLLM / clock overrides."""

from datetime import datetime

import pytest

from app.agent import router as questions_router
from app.agent.llm import FakeLLM, get_llm
from app.agent.periods import ET, now_et
from app.main import app
from tests.agent.helpers import make_fixture_user


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """The limiter's state is process-wide; clear it so one test can't fail because of another."""
    questions_router._recent.clear()
    yield
    questions_router._recent.clear()


@pytest.fixture
def fixture_user(client, db):
    """A user with fixtures/sample_transactions.csv imported into Checking and labeled; (headers, user_id, account_id)."""
    return make_fixture_user(client, db, "fixture@umd.edu")


@pytest.fixture
def use_llm(client):
    """Install a FakeLLM for the router: use_llm([...]) returns the FakeLLM."""
    def install(script, today=datetime(2026, 10, 19, 12, 0, tzinfo=ET)):
        """Override get_llm and now_et for the API."""
        fake = FakeLLM(script)
        app.dependency_overrides[get_llm] = lambda: fake
        app.dependency_overrides[now_et] = lambda: today
        return fake
    return install
