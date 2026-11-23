from datetime import UTC, datetime, timedelta

import jwt

from app import config
from tests.conftest import register


# Happy path: register, log in, /me
def test_register_login_me(client):
    """Register, log in, and fetch /auth/me; the email is stored lowercased."""
    headers = register(client, "Terp@UMD.edu")
    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == "terp@umd.edu"


# Passwords are never stored in plain text
def test_password_is_hashed(client, db):
    """The plain-text password is never stored in the database."""
    from app.models import User

    register(client, "a@umd.edu", password="correct-horse-1")
    user = db.query(User).filter_by(email="a@umd.edu").one()
    assert "correct-horse-1" not in user.password_hash


# Duplicate emails, ignoring case
def test_register_duplicate_email_case_insensitive(client):
    """Registering an email that differs only in case returns 409."""
    register(client, "a@umd.edu")
    r = client.post("/auth/register", json={"email": "A@umd.edu", "password": "another-pass-1"})
    assert r.status_code == 409


# Signup input validation
def test_register_rejects_short_password_and_bad_email(client):
    """Passwords under 8 characters and malformed emails are rejected with 422."""
    assert client.post("/auth/register", json={"email": "a@umd.edu", "password": "short"}).status_code == 422
    assert client.post("/auth/register", json={"email": "not-an-email", "password": "long-enough-1"}).status_code == 422


# Login doesn't reveal which emails exist
def test_login_wrong_password_and_unknown_email_look_the_same(client):
    """A wrong password and an unknown email give the identical 401 response, so emails can't be probed."""
    register(client, "a@umd.edu")
    wrong = client.post("/auth/login", data={"username": "a@umd.edu", "password": "wrong-password"})
    unknown = client.post("/auth/login", data={"username": "nobody@umd.edu", "password": "wrong-password"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# Missing or garbage tokens
def test_protected_routes_need_a_valid_token(client):
    """Requests with no token or a garbage token get 401."""
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


# Expired tokens
def test_expired_token_rejected(client, db):
    """A correctly signed but expired token gets 401."""
    from app.models import User

    register(client, "a@umd.edu")
    user_id = db.query(User).filter_by(email="a@umd.edu").one().id
    expired = jwt.encode(
        {"sub": str(user_id), "exp": datetime.now(UTC) - timedelta(minutes=1)},
        config.JWT_SECRET,
        algorithm=config.JWT_ALGORITHM,
    )
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


# Forged tokens
def test_token_signed_with_other_secret_rejected(client, db):
    """A token signed with a different secret (a forgery) gets 401."""
    from app.models import User

    register(client, "a@umd.edu")
    user_id = db.query(User).filter_by(email="a@umd.edu").one().id
    forged = jwt.encode({"sub": str(user_id)}, "some-other-secret-that-is-long-enough-123", algorithm="HS256")
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
