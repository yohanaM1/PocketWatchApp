"""Build frontend/src/test/fixtures/answers.json from tests/agent/cases.py.

Run from backend/ after changing a template or the result shape:  python -m tests.agent.write_ui_fixtures
tests/agent/test_ui_contract.py fails until the checked-in file matches.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")

from app.agent.replies import FAILED_REPLY, FIXED_REPLIES  # noqa: E402
from app.agent.router import QuestionOut  # noqa: E402
from tests.agent.cases import BY_NAME, CASES, render  # noqa: E402

PATH = Path(__file__).resolve().parents[3] / "frontend" / "src" / "test" / "fixtures" / "answers.json"
CREATED = "2026-10-19T12:00:00"


def _out(i: int, question: str, status: str, **fields) -> dict:
    """A QuestionOut as the API serializes it, built through the real response model."""
    row = SimpleNamespace(id=i, question=question, verification_status=status, answer=None, extra_sentence=None,
                          plan=None, slots=None, final_results=None, attempts=1, as_of="2026-10-19",
                          created_at=CREATED)
    for k, v in fields.items():
        setattr(row, k, v)
    return QuestionOut.model_validate(row).model_dump(mode="json")


def build() -> dict:
    """name -> QuestionOut: every template case (verified), plus unverified, failed and a fixed reply."""
    out = {}
    for i, case in enumerate(CASES, 1):
        text, slots, plan = render(case)
        extra = "Nice work keeping track." if case.name == "single_dining_sep" else None
        out[case.name] = _out(i, case.question, "verified", answer=text, extra_sentence=extra, plan=plan,
                              slots=slots, final_results=[case.result], as_of=case.today.isoformat())
    base = BY_NAME["uncat_one"]
    _, slots, plan = render(base)
    n = len(CASES)
    out["unverified"] = _out(n + 1, base.question, "unverified", plan=plan, slots=slots,
                             final_results=[base.result])
    out["failed"] = _out(n + 2, "How much did I spend on dining in September?", "failed", answer=FAILED_REPLY)
    out["fixed_reply"] = _out(n + 3, "September vs October?", "verified", answer=FIXED_REPLIES["unsupported"],
                              plan={"intent": "no_data", "label": "unsupported", "source": "code"})
    return out


def dumps(data: dict) -> str:
    """Stable, compact JSON text: one answer per line (keeps non-ASCII, e.g. the emoji, as-is)."""
    lines = [f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in data.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


if __name__ == "__main__":
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(dumps(build()), encoding="utf-8")
    print(f"wrote {PATH}")
