from tests.conftest import register


# Default account on signup
def test_register_creates_default_checking_account(client):
    """A new user starts with exactly one Checking account."""
    headers = register(client, "a@umd.edu")
    accounts = client.get("/accounts", headers=headers).json()
    assert [(a["name"], a["type"]) for a in accounts] == [("Checking", "checking")]


# Account creation
def test_create_account(client):
    """Creating an account trims the name and adds it to the user's list."""
    headers = register(client, "a@umd.edu")
    r = client.post("/accounts", json={"name": "  Discover Card ", "type": "credit"}, headers=headers)
    assert r.status_code == 201
    assert r.json()["name"] == "Discover Card"
    assert len(client.get("/accounts", headers=headers).json()) == 2


# Account input validation
def test_create_account_validation(client):
    """A blank name or unknown account type is rejected with 422."""
    headers = register(client, "a@umd.edu")
    assert client.post("/accounts", json={"name": "   ", "type": "credit"}, headers=headers).status_code == 422
    assert client.post("/accounts", json={"name": "X", "type": "crypto"}, headers=headers).status_code == 422


# Per-user account scoping
def test_users_only_see_their_own_accounts(client):
    """GET /accounts never shows another user's accounts."""
    alice = register(client, "alice@umd.edu")
    bob = register(client, "bob@umd.edu")
    client.post("/accounts", json={"name": "Alice Savings", "type": "savings"}, headers=alice)
    bob_accounts = client.get("/accounts", headers=bob).json()
    assert [a["name"] for a in bob_accounts] == ["Checking"]
