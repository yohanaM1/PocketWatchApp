"""The LLM interface: send messages and tools, get back tool calls. Real OpenAI-compatible client plus FakeLLM."""

import functools
import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx2 as httpx  # the httpx2 package's module name

from app import config


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as the model sent it; parsed by the loop


@dataclass
class LLMReply:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str | None = None


class LLMError(Exception):
    """Transport/API failure; the loop maps it to status `failed`."""


class LLM(Protocol):
    def complete(self, messages: list[dict], tools: list[dict] | None,
                 tool_choice: str, timeout: float) -> LLMReply: ...


class OpenAICompatibleLLM:
    """Client for an OpenAI-style /chat/completions endpoint (assumed shape of the course API)."""

    def __init__(self, base_url: str, api_key: str, model: str, transport: httpx.BaseTransport | None = None):
        """One pooled HTTP client per instance; `transport` lets tests use httpx.MockTransport."""
        self._http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                  transport=transport)
        self._model = model

    def complete(self, messages, tools, tool_choice, timeout) -> LLMReply:
        """One chat completion; every failure (transport, status, malformed body) becomes LLMError."""
        body = {"model": self._model, "messages": messages}
        if config.LLM_TEMPERATURE != "":
            body["temperature"] = float(config.LLM_TEMPERATURE)
        if tools:
            body["tools"] = tools
            # the fallback comes from config (set once from check_llm.py), not a per-request flag
            body["tool_choice"] = "auto" if tool_choice == "required" and config.LLM_TOOL_CHOICE == "auto" \
                else tool_choice
            body["parallel_tool_calls"] = False
        try:
            r = self._http.post("/chat/completions", json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise LLMError(type(e).__name__) from None  # never include headers/key in the message
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}")  # keep the status (401/429/5xx), never the body
        try:  # a malformed reply is an LLMError, not a crash
            msg = r.json()["choices"][0]["message"]
            calls = [_tool_call(c, i) for i, c in enumerate(msg.get("tool_calls") or [])]
            text = _text(msg.get("content"))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError(f"malformed response: {type(e).__name__}") from None
        return LLMReply(tool_calls=calls, text=text)


def _tool_call(c: dict, i: int) -> ToolCall:
    """Normalize provider differences: arguments sent as an object, missing call ids."""
    args = c["function"].get("arguments") or "{}"
    if not isinstance(args, str):
        args = json.dumps(args)
    return ToolCall(c.get("id") or f"call_{i}", c["function"]["name"], args)


def _text(content) -> str | None:
    """Plain string, or the text parts of a content-part list."""
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content


class FakeLLM:
    """Plays back a scripted list of replies (or exceptions) and records what it was sent."""

    def __init__(self, script: list[LLMReply | Exception]):
        """Keep a copy of the script; requests are recorded in order."""
        self.script = list(script)
        self.requests: list[dict] = []

    def complete(self, messages, tools, tool_choice, timeout) -> LLMReply:
        """Record the request and return (or raise) the next scripted item."""
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Unconfigured:
    """Stands in when LLM_* aren't set, so the app and CI start; every call becomes status `failed`."""

    def complete(self, messages, tools, tool_choice, timeout) -> LLMReply:
        """Always fail: there is no LLM to call."""
        raise LLMError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL not configured")


@functools.cache
def get_llm() -> LLM:
    """One client per process, so connections are pooled and nothing leaks per request."""
    if not (config.LLM_BASE_URL and config.LLM_API_KEY and config.LLM_MODEL):
        return _Unconfigured()
    return OpenAICompatibleLLM(config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL)
