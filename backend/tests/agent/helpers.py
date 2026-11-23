"""Builders for FakeLLM scripts, a fake clock, and the fixture-user setup shared by agent tests."""

import json
from pathlib import Path

from app.agent.llm import FakeLLM, LLMReply, ToolCall
from app.devdata import apply_labels
from tests.conftest import register

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
FIXTURE_CSV = (FIXTURES / "sample_transactions.csv").read_bytes()
LABELS_CSV = (FIXTURES / "sample_labels.csv").read_text()


def call(name: str, **args) -> LLMReply:
    """A reply with one tool call: call("final_plan", intent="category_total", period="2026-09", categories=["Dining"])."""
    return LLMReply(tool_calls=[ToolCall(f"call_{name}", name, json.dumps(args))])


def calls(*replies: LLMReply) -> LLMReply:
    """Several tool calls in one reply (ids made unique), for the 'both tools' and 'ignored call' tests."""
    tcs = [ToolCall(f"call_{i}", c.name, c.arguments) for i, r in enumerate(replies) for c in r.tool_calls]
    return LLMReply(tool_calls=tcs)


def text(s: str) -> LLMReply:
    """A text-only reply (no tool call), or the extra sentence."""
    return LLMReply(text=s)


def plan(period="2026-09", categories=("Dining",), **extra) -> LLMReply:
    """A final_plan call for category_total."""
    cats = categories if isinstance(categories, str) else list(categories)
    return call("final_plan", intent="category_total", period=period, categories=cats, **extra)


def count_plan(period="2026-10", categories=("Dining",), **extra) -> LLMReply:
    """A final_plan call for transaction_count."""
    cats = categories if isinstance(categories, str) else list(categories)
    return call("final_plan", intent="transaction_count", period=period, categories=cats, **extra)


class FakeClock:
    """A monotonic clock tests move by hand."""

    def __init__(self, t: float = 1000.0):
        """Start at an arbitrary time."""
        self.t = t

    def __call__(self) -> float:
        """The current fake time."""
        return self.t


class TimedFakeLLM(FakeLLM):
    """A FakeLLM that advances a FakeClock by a fixed number of seconds per call (or per-call list)."""

    def __init__(self, script, clock: FakeClock, seconds):
        """`seconds` is a number, or a list consumed one per call."""
        super().__init__(script)
        self.clock, self.seconds = clock, seconds

    def complete(self, messages, tools, tool_choice, timeout):
        """Advance the clock, then behave like FakeLLM."""
        self.clock.t += self.seconds.pop(0) if isinstance(self.seconds, list) else self.seconds
        return super().complete(messages, tools, tool_choice, timeout)


def checking_id(client, headers) -> int:
    """The id of the Checking account that registration creates."""
    return client.get("/accounts", headers=headers).json()[0]["id"]


def upload(client, headers, account_id: int, content: bytes):
    """POST a CSV to the import endpoint."""
    r = client.post(f"/accounts/{account_id}/imports", headers=headers,
                    files={"file": ("transactions.csv", content, "text/csv")})
    assert r.status_code == 200, r.text
    return r


def make_fixture_user(client, db, email: str, csv_bytes: bytes = FIXTURE_CSV, labels: str | None = LABELS_CSV):
    """Register, import the fixture into Checking, apply labels, commit. Returns (headers, user_id, account_id)."""
    headers = register(client, email)
    account_id = checking_id(client, headers)
    upload(client, headers, account_id, csv_bytes)
    if labels is not None:
        apply_labels(db, account_id, csv_bytes, labels)
    db.commit()  # the loop commits before LLM calls; uncommitted labels could be lost on a later rollback
    user_id = client.get("/auth/me", headers=headers).json()["id"]
    return headers, user_id, account_id


def subset(csv_bytes: bytes, labels: str, keep) -> tuple[bytes, str]:
    """The fixture rows (and their labels, renumbered) whose date string satisfies keep(date)."""
    lines = csv_bytes.decode().splitlines()
    label_lines = labels.splitlines()
    out, out_labels = [lines[0]], [label_lines[0]]
    for line, label in zip(lines[1:], label_lines[1:]):
        if keep(line.split(",", 1)[0]):
            out.append(line)
            parts = label.split(",", 1)
            out_labels.append(f"{len(out) - 1},{parts[1]}")
    return ("\n".join(out) + "\n").encode(), "\n".join(out_labels) + "\n"
