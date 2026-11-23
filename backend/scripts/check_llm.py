"""Manual smoke test against the real course LLM. Never run in CI.

Run from backend/ with LLM_BASE_URL, LLM_API_KEY and LLM_MODEL set in .env:  python -m scripts.check_llm

Sends "How much did I spend on dining in September?" with the real tool specs and reports what the
API accepts, ending with the .env values to use.
"""

import json
import sys
import time
from datetime import date

import httpx2 as httpx

from app import config
from app.agent import prompts
from app.agent.plan import FinalPlan, tool_specs

QUESTION = "How much did I spend on dining in September?"


def post(http: httpx.Client, body: dict) -> tuple[int, dict | str, float]:
    """POST /chat/completions; (status, parsed body or text, seconds)."""
    t = time.monotonic()
    r = http.post("/chat/completions", json=body, timeout=30)
    try:
        data = r.json()
    except ValueError:
        data = r.text[:300]
    return r.status_code, data, time.monotonic() - t


def main() -> int:
    """Probe tool_choice, temperature, the schema, tool-call ids/arguments and latency."""
    if not (config.LLM_BASE_URL and config.LLM_API_KEY and config.LLM_MODEL):
        print("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in .env first.", file=sys.stderr)
        return 1
    http = httpx.Client(base_url=config.LLM_BASE_URL, headers={"Authorization": f"Bearer {config.LLM_API_KEY}"})
    messages = [{"role": "system", "content": prompts.system(date.today())}, {"role": "user", "content": QUESTION}]
    base = {"model": config.LLM_MODEL, "messages": messages, "tools": tool_specs(), "parallel_tool_calls": False}
    env = {}

    status, data, _ = post(http, {**base, "tool_choice": "required", "temperature": 0})
    if status != 200:
        print(f"tool_choice=required + temperature=0 -> HTTP {status}: {data}")
        status, data, _ = post(http, {**base, "tool_choice": "required"})
        if status == 200:
            env["LLM_TEMPERATURE"] = ""
        else:
            status, data, _ = post(http, {**base, "tool_choice": "auto", "temperature": 0})
            env["LLM_TOOL_CHOICE"] = "auto"
            if status != 200:
                status, data, _ = post(http, {**base, "tool_choice": "auto"})
                env["LLM_TEMPERATURE"] = ""
    print(f"schema with anyOf/pattern/format accepted: {status == 200}")
    if status != 200:
        print(f"HTTP {status}: {data}")
        return 1

    msg = data["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    print(f"\nraw tool calls:\n{json.dumps(calls, indent=2)}")
    print(f"text content: {msg.get('content')!r}")
    print(f"tool calls have ids: {all(c.get('id') for c in calls) if calls else 'n/a'}")
    print(f"arguments sent as: {sorted({type(c['function'].get('arguments')).__name__ for c in calls}) or 'n/a'}")
    for c in calls:
        if c["function"]["name"] == "final_plan":
            args = c["function"]["arguments"]
            try:
                plan = FinalPlan.model_validate_json(args if isinstance(args, str) else json.dumps(args))
                print(f"final_plan validates: {plan}")
            except ValueError as e:
                print(f"final_plan does NOT validate (the schema isn't enforced server-side): {e}")

    times = []
    for _ in range(3):
        _, _, dt = post(http, {**base, "tool_choice": env.get("LLM_TOOL_CHOICE", "required"),
                               **({} if env.get("LLM_TEMPERATURE") == "" else {"temperature": 0})})
        times.append(dt)
    print(f"\nround trips: {', '.join(f'{t:.1f}s' for t in times)} "
          f"(budget {config.AGENT_TIME_LIMIT_S:.0f}s total, {config.LLM_CALL_TIMEOUT_S:.0f}s per call)")

    print("\nUse in .env:")
    print(f"LLM_TOOL_CHOICE={env.get('LLM_TOOL_CHOICE', 'required')}")
    print(f"LLM_TEMPERATURE={env.get('LLM_TEMPERATURE', '0')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
