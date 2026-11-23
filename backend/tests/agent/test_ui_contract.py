"""The frontend's fixture answers must match what the backend produces today."""

from tests.agent.write_ui_fixtures import PATH, build, dumps


def test_ui_fixture_file_is_current():
    """Regenerate with `python -m tests.agent.write_ui_fixtures` if this fails."""
    assert PATH.exists(), "run python -m tests.agent.write_ui_fixtures"
    assert PATH.read_text(encoding="utf-8") == dumps(build())


def test_fixture_covers_every_status_and_an_emoji():
    """The UI tests need every status, a fixed reply, and a non-BMP character before a slot."""
    data = build()
    assert {a["status"] for a in data.values()} == {"verified", "unverified", "failed"}
    assert any(a["plan"] and a["plan"].get("intent") == "no_data" for a in data.values())
    assert any(any(ord(ch) > 0xFFFF for ch in (a["answer"] or "")) for a in data.values())
