import pytest

from scripts.seed_demo_user import parse_credentials


# Credentials from .env
def test_reads_demo_email_and_password_from_env():
    """With no flags, DEMO_EMAIL / DEMO_PASSWORD are used."""
    args = parse_credentials([], {"DEMO_EMAIL": "me@umd.edu", "DEMO_PASSWORD": "from-env-123"})
    assert (args.email, args.password) == ("me@umd.edu", "from-env-123")


# Flags win
def test_flags_override_env():
    """--email / --password take precedence over the environment."""
    args = parse_credentials(["--email", "x@umd.edu", "--password", "flag-pass-1"],
                             {"DEMO_EMAIL": "me@umd.edu", "DEMO_PASSWORD": "from-env-123"})
    assert (args.email, args.password) == ("x@umd.edu", "flag-pass-1")


# Defaults
def test_email_defaults_to_demo_example_com():
    """An empty or missing DEMO_EMAIL falls back to demo@example.com."""
    assert parse_credentials([], {"DEMO_EMAIL": "", "DEMO_PASSWORD": "from-env-123"}).email == "demo@example.com"


# Missing or short password
@pytest.mark.parametrize("env", [{}, {"DEMO_PASSWORD": ""}, {"DEMO_PASSWORD": "short"}])
def test_missing_or_short_password_is_a_usage_error(env, capsys):
    """No password anywhere, or under 8 characters, exits with a message that points to DEMO_PASSWORD."""
    with pytest.raises(SystemExit) as exc:
        parse_credentials([], env)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "DEMO_PASSWORD" in err or "at least 8 characters" in err


# --reset
def test_reset_is_off_unless_asked_for():
    """Re-seeding keeps the demo user's data unless --reset is passed."""
    env = {"DEMO_PASSWORD": "from-env-123"}
    assert parse_credentials([], env).reset is False
    assert parse_credentials(["--reset"], env).reset is True
