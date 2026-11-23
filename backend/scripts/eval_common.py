"""Shared by the evaluation scripts: record real LLM replies to a file, or replay them without calling the API.

A recording is {key: [reply, ...]}, one list per question or batch, in call order. A reply is
{"text": ..., "tool_calls": [{"id", "name", "arguments"}]} or {"error": "..."} for an API failure.
Re-record whenever a prompt, a tool schema or the batching changes; a replay of a stale recording
measures the old prompt.
"""

import hashlib
import json
import statistics
from pathlib import Path

from app.agent.llm import LLM, LLMError, LLMReply, ToolCall

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
REPO = Path(__file__).resolve().parents[2]


class StaleRecording(SystemExit):
    """The code now sends a different request than the one the recording answered."""


def fingerprint(messages, tools) -> str:
    """A short hash of exactly what was sent, so a replay can tell when the request has changed."""
    return hashlib.sha256(json.dumps([messages, tools], sort_keys=True).encode()).hexdigest()[:16]


class RecordingLLM:
    """Passes calls to a real LLM and keeps every reply (or error) under the current key."""

    def __init__(self, llm: LLM):
        """Wrap `llm`; call .use(key) before each question or batch."""
        self.llm, self.key, self.replies = llm, "", {}

    def use(self, key: str) -> "RecordingLLM":
        """Record the following calls under `key`."""
        self.key = key
        self.replies.setdefault(key, [])
        return self

    def complete(self, messages, tools, tool_choice, timeout) -> LLMReply:
        """Forward the call and record what came back."""
        sent = fingerprint(messages, tools)
        try:
            reply = self.llm.complete(messages, tools, tool_choice, timeout)
        except LLMError as e:
            self.replies[self.key].append({"error": str(e), "request": sent})
            raise
        self.replies[self.key].append({"request": sent, "text": reply.text, "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]})
        return reply

    def save(self, path: Path) -> None:
        """Write the recording as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.replies, indent=1) + "\n")


class ReplayLLM:
    """Plays back a recording. Running past the recorded replies is an LLMError, like a dead API."""

    def __init__(self, path: Path):
        """Load the recording; call .use(key) before each question or batch."""
        self.replies = json.loads(path.read_text())
        self.key, self.queue = "", []

    def use(self, key: str) -> "ReplayLLM":
        """Play back the replies recorded under `key`."""
        self.key, self.queue = key, list(self.replies.get(key, []))
        return self

    def complete(self, messages, tools, tool_choice, timeout) -> LLMReply:
        """The next recorded reply for the current key."""
        if not self.queue:
            raise LLMError("replay: no recorded reply left")
        r = self.queue.pop(0)
        if "request" in r and r["request"] != fingerprint(messages, tools):
            raise StaleRecording(
                f"Recording is stale: the request for '{self.key}' changed since it was recorded (a prompt, the "
                "batching or merchant cleanup changed). Re-record with --record instead of --replay.")
        if "error" in r:
            raise LLMError(r["error"])
        return LLMReply(text=r["text"], tool_calls=[ToolCall(c["id"], c["name"], c["arguments"])
                                                    for c in r["tool_calls"]])


def make_llm(record: Path | None, replay: Path | None):
    """The LLM for an evaluation run: replay a file, or the real API (recorded when `record` is set)."""
    if replay:
        return ReplayLLM(replay)
    from app import config
    from app.agent.llm import get_llm
    if not (config.LLM_BASE_URL and config.LLM_API_KEY and config.LLM_MODEL):
        raise SystemExit("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env, or pass --replay FILE.")
    return RecordingLLM(get_llm())


def pct(n: int, d: int) -> str:
    """'n/d (x.x%)', or 'n/0' when there's nothing to divide by."""
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else f"{n}/0"


def latency_summary(ms: list[int]) -> dict:
    """Median and 95th percentile of a list of latencies in milliseconds."""
    if not ms:
        return {}
    s = sorted(ms)
    return {"p50_ms": int(statistics.median(s)), "p95_ms": s[min(len(s) - 1, int(0.95 * len(s)))], "max_ms": s[-1]}
