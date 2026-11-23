"""OpenAICompatibleLLM parsing via httpx.MockTransport (no network), and the tool specs."""

import json

import httpx2 as httpx
import pytest

from app import config
from app.agent.llm import LLMError, OpenAICompatibleLLM
from app.agent.plan import tool_specs


def client(handler) -> OpenAICompatibleLLM:
    """A client whose HTTP calls go to `handler`."""
    return OpenAICompatibleLLM("http://llm.test/v1", "sk-test", "m", transport=httpx.MockTransport(handler))


def reply(message: dict, status: int = 200):
    """A handler returning one chat-completions message."""
    return lambda req: httpx.Response(status, json={"choices": [{"message": message}]})


def complete(llm, tools=None):
    """One call with a trivial message."""
    return llm.complete([{"role": "user", "content": "hi"}], tools, "required", timeout=5)


@pytest.mark.parametrize("handler,match", [
    (lambda req: httpx.Response(429, text="slow down, key=sk-test"), "HTTP 429"),
    (lambda req: httpx.Response(200, text="not json"), "malformed"),
    (lambda req: httpx.Response(200, json={"nope": []}), "malformed"),
    (lambda req: httpx.Response(200, json={"choices": []}), "malformed"),
    (lambda req: httpx.Response(200, json={"choices": [{"message": {"tool_calls": [{"x": 1}]}}]}), "malformed"),
])
def test_errors_become_llm_error(handler, match):
    """Every failure is an LLMError whose message never contains the body or key."""
    with pytest.raises(LLMError) as e:
        complete(client(handler))
    assert match in str(e.value) and "sk-test" not in str(e.value)


def test_transport_error():
    """A connection error is an LLMError named by type."""
    def boom(req):
        """Simulate a dropped connection."""
        raise httpx.ConnectError("refused")
    with pytest.raises(LLMError, match="ConnectError"):
        complete(client(boom))


def test_arguments_object_and_missing_ids():
    """Arguments sent as an object are re-encoded; missing ids get call_<i>."""
    msg = {"content": None, "tool_calls": [{"function": {"name": "final_plan", "arguments": {"a": 1}}},
                                           {"id": "x9", "function": {"name": "no_data_needed", "arguments": "{}"}}]}
    r = complete(client(reply(msg)))
    assert [(c.id, c.name, json.loads(c.arguments)) for c in r.tool_calls] == \
        [("call_0", "final_plan", {"a": 1}), ("x9", "no_data_needed", {})]


def test_content_parts():
    """A content-part list becomes its joined text."""
    r = complete(client(reply({"content": [{"type": "text", "text": "Nice "}, {"type": "text", "text": "work."}]})))
    assert r.text == "Nice work." and r.tool_calls == []


def test_request_body(monkeypatch):
    """Tools, tool_choice (with the config fallback), parallel_tool_calls and temperature are sent as configured."""
    seen = []

    def handler(req):
        """Record the body and reply with text."""
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    llm = client(handler)
    complete(llm, tools=tool_specs())
    assert seen[-1]["tool_choice"] == "required" and seen[-1]["parallel_tool_calls"] is False
    assert seen[-1]["temperature"] == 0.0
    monkeypatch.setattr(config, "LLM_TOOL_CHOICE", "auto")
    monkeypatch.setattr(config, "LLM_TEMPERATURE", "")
    complete(llm, tools=tool_specs())
    assert seen[-1]["tool_choice"] == "auto" and "temperature" not in seen[-1]
    complete(llm)
    assert "tools" not in seen[-1] and "tool_choice" not in seen[-1]


def test_tool_specs_plain_and_final_tools_only(monkeypatch):
    """No $ref/$defs; only final_plan, list_plan and no_data_needed unless exploration is on."""
    specs = tool_specs()
    assert "$ref" not in json.dumps(specs) and "$defs" not in json.dumps(specs)
    assert [s["function"]["name"] for s in specs] == ["final_plan", "list_plan", "no_data_needed"]
    monkeypatch.setattr(config, "AGENT_EXPLORATION", True)
    assert [s["function"]["name"] for s in tool_specs()] == ["final_plan", "list_plan", "no_data_needed",
                                                             "group_by_category", "get_transactions",
                                                             "compare_periods"]
