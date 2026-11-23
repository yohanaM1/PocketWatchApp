"""Orchestrator: pre-check, LLM rounds, plan check, server-side re-run, render, verify, extra sentence.

The numbers in an answer come only from re-running the final, checked plan, never from anything
the LLM saw or said.
"""

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.agent import plan_check, prompts, replies, templates, verifier
from app.agent.llm import LLM, LLMError, LLMReply, ToolCall
from app.agent.periods import PeriodError, ResolvedPeriod, resolve
from app.agent.plan import (EXPLORATION_TOOLS, FINAL_TOOLS, TOOL_ARG_MODELS, FinalPlan, GetTransactionsArgs,
                            GroupByCategoryArgs, ListPlan, NoDataNeededArgs, tool_specs)
from app.agent.spending import SPENDING_CATEGORIES
from app.agent.tools import LIST_TOOLS, TOOLS
from app.models import Account, Transaction
from app.models import VerificationStatus as VS

EXPLORATION = set(EXPLORATION_TOOLS)

NUMBER_WORDS = re.compile(r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                          r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|"
                          r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|"
                          r"dozen|half|double|twice|triple|percent)\b", re.I)

# Claims without numbers: the extra-sentence LLM sees only this answer, so any comparison,
# ranking or "usual" is invented. False positives just drop the sentence.
CLAIM_WORDS = re.compile(r"\b(more|less|fewer|higher|lower|usual|usually|normal|average|typical|top|most|"
                         r"least|biggest|largest|smallest|increase\w*|decrease\w*|compared?|than|up|down|"
                         r"first|second|third|last|record|budget|over|under|spike|jump\w*|drop\w*)\b", re.I)


@dataclass
class Outcome:
    status: VS
    answer: str | None = None
    extra: str | None = None
    plan: dict | None = None
    slots: list[dict] | None = None
    final_results: list[dict] | None = None
    tool_log: list[dict] = field(default_factory=list)
    failure_reason: str | None = None
    attempts: int = 1
    latency_ms: int | None = None  # stored for audit and evaluation


class _Fail(Exception):
    """Ends the loop with status failed."""


class _Retry(Exception):
    """Plan rejection: the reason goes back to the LLM once."""

    def __init__(self, reason: str, call_id: str):
        """Keep the call id so the reason is sent as that call's tool result."""
        super().__init__(reason)
        self.call_id = call_id


def answer_question(db: Session, user_id: int, question: str, llm: LLM, today: date, *,
                    clock: Callable[[], float] = time.monotonic) -> Outcome:
    """Answer one question for one user; never raises for LLM problems (they become `failed`)."""
    started = clock()
    deadline = started + config.AGENT_TIME_LIMIT_S
    out = Outcome(status=VS.FAILED)
    try:
        return _answer(db, user_id, question, llm, today, out, clock, deadline)
    finally:
        out.latency_ms = int((clock() - started) * 1000)


def _answer(db, user_id, question, llm, today, out, clock, deadline) -> Outcome:
    """Pre-check, then up to two attempts of LLM rounds + finish."""
    # Code-only pre-check: unsupported or future questions never reach the LLM.
    known_merchants = _known_merchants(db, user_id)
    try:
        facts = plan_check.extract_facts(question, today, known_merchants)
    except plan_check.Unanswerable as u:
        return _fixed(out, u.label, source="code")

    messages = [{"role": "system", "content": prompts.system(today)},
                {"role": "user", "content": question}]

    def left() -> float:
        """Seconds until the deadline (may be negative)."""
        return deadline - clock()

    def remaining() -> float:
        """Seconds until the deadline; raises _Fail once it has passed."""
        if left() <= 0:
            raise _Fail("time limit")
        return left()

    def ask() -> LLMReply:
        """One planning call to the LLM."""
        db.commit()  # end the read transaction so the DB connection goes back to the pool while we wait
        for attempt in (1, 2):
            try:
                return llm.complete(messages, tool_specs(), "required",
                                    timeout=min(config.LLM_CALL_TIMEOUT_S, remaining()))
            except LLMError as e:
                # Some course-API calls hang well past the usual few seconds; a fresh call usually answers.
                if attempt == 1 and _transient(e) and left() >= config.AGENT_RETRY_MIN_TIME_S:
                    continue
                raise _Fail(f"llm error: {e}")

    try:
        last_reason = None
        for attempt in (1, 2):
            if attempt == 2 and left() < config.AGENT_RETRY_MIN_TIME_S:
                break  # no time for a real retry
            out.attempts = attempt
            final_call = _run_rounds(db, user_id, today, ask, messages, out)  # returns ToolCall or Outcome
            if isinstance(final_call, Outcome):
                return final_call  # no_data_needed
            try:
                if final_call.name == "list_plan":
                    return _finish_list(db, user_id, question, today, facts, final_call, out)
                return _finish(db, user_id, question, today, facts, final_call, llm, left, out)
            except _Retry as r:
                last_reason = str(r)
                messages.append(_tool_msg(r.call_id, {"error": last_reason,
                                                      "instruction": "Fix the plan and call final_plan again."}))
        # Plan rejected twice: nothing was ever rendered, so this is `failed`, not `unverified`.
        raise _Fail(f"plan rejected: {last_reason}")
    except _Fail as f:
        out.status, out.failure_reason = VS.FAILED, _truncate(str(f))
        return out


def _fixed(out: Outcome, label: str, *, source: str) -> Outcome:
    """A fixed reply: no facts, so `verified`. `source` records whether code or the LLM chose it."""
    out.status, out.answer = VS.VERIFIED, replies.FIXED_REPLIES[label]
    out.plan = {"intent": "no_data", "label": label, "source": source}
    return out


def _transient(e: LLMError) -> bool:
    """A timeout or server error, worth one more try; auth and request errors (4xx) are not."""
    msg = str(e)
    return "Timeout" in msg or msg.startswith("HTTP 5")


def _truncate(s: str | None) -> str | None:
    """Fit the failure_reason column (MySQL strict mode rejects longer values)."""
    return None if s is None else s[: config.FAILURE_REASON_MAX_CHARS]


def _run_rounds(db, user_id, today, ask, messages, out) -> ToolCall | Outcome:
    """LLM rounds until final_plan (returns its call) or no_data_needed (returns the fixed Outcome)."""
    nudged = False
    for _ in range(config.AGENT_MAX_ROUNDS):
        reply = ask()
        if not reply.tool_calls:
            if nudged:
                raise _Fail("no tool call after retry")
            nudged = True
            messages.append({"role": "user", "content": prompts.MUST_CALL_TOOL})
            continue
        messages.append(_assistant_msg(reply))
        names = {c.name for c in reply.tool_calls}
        if len(names & set(FINAL_TOOLS)) > 1:
            # contradictory; answer every call id with an error and use a round
            for c in reply.tool_calls:
                messages.append(_tool_msg(c.id, {"error": "call exactly one of final_plan, list_plan or "
                                                          "no_data_needed"}))
            continue
        if "no_data_needed" in names:
            c = next(c for c in reply.tool_calls if c.name == "no_data_needed")
            try:
                args = NoDataNeededArgs.model_validate_json(c.arguments)
            except ValidationError as e:
                # answer EVERY call id in this reply, or the next request is rejected
                for other in reply.tool_calls:
                    messages.append(_tool_msg(other.id, {"error": _short(e)} if other is c else {"ignored": True}))
                continue
            out.tool_log.append({"phase": "final", "tool": c.name, "args": args.model_dump()})
            return _fixed(out, args.label, source="llm")
        final = next((c for c in reply.tool_calls if c.name in ("final_plan", "list_plan")), None)
        for c in reply.tool_calls:
            if c is final:
                continue
            if final is not None or c.name not in EXPLORATION or not config.AGENT_EXPLORATION:
                messages.append(_tool_msg(c.id, {"ignored": True}))
                continue
            result = _run_exploration(db, user_id, today, c)  # validation/PeriodError -> {"error": ...}
            out.tool_log.append({"phase": "explore", "tool": c.name, "args": _raw_args(c), "result": result})
            messages.append(_tool_msg(c.id, result))
        if final is not None:
            return final
    raise _Fail("round limit")


def _finish(db, user_id, question, today, facts, call, llm, left, out) -> Outcome:
    """Validate and check the plan, re-run it server-side, render, verify, add the extra sentence."""
    try:
        plan = FinalPlan.model_validate_json(call.arguments)
        plan = plan.model_copy(update={"categories": _dedupe(plan.categories)})
        plan = plan_check.check_plan(facts, question, plan, today)
        resolved = resolve(plan.period, today)
    except PeriodError as e:
        if e.reason == "too_long":  # the user asked for > 1 year: say so, don't retry
            return _fixed(out, "range_too_long", source="code")
        if e.reason == "future" and not (facts.exact or facts.overlap):
            # "next month", "the rest of the year": the question names no past period the checker
            # recognized, so the future plan is what the user asked for. Fixed reply, no retry.
            return _fixed(out, "future_period", source="code")
        raise _Retry(_short(e), call.id)  # e.g. "December" planned as 2026-12: the LLM's mistake
    except (ValidationError, plan_check.PlanRejected) as e:
        raise _Retry(_short(e), call.id)

    # Re-run server-side: the answer uses ONLY these results.
    cats = None if plan.categories == "all_spending" else plan.categories
    if plan.intent == "category_total":
        res = TOOLS["group_by_category"](db, user_id, GroupByCategoryArgs(period=plan.period, categories=cats), today)
        text, slots = templates.render_category_total(plan, res)
    else:
        # all_spending means the 11 spending categories, never "everything" (payroll, transfers)
        args = GetTransactionsArgs(period=plan.period, categories=cats or list(SPENDING_CATEGORIES))
        res = TOOLS["get_transactions"](db, user_id, args, today)
        text, slots = templates.render_transaction_count(plan, res)

    pj = plan_json(plan, resolved, today)
    slot_json = [asdict(s) for s in slots]
    out.tool_log.append({"phase": "final", "tool": "final_plan", "args": json.loads(call.arguments), "result": res})
    out.plan, out.slots, out.final_results = pj, slot_json, [res]  # stored for every outcome below

    v = verifier.verify(text, None, slot_json, [res], question, pj)
    if not v.ok:
        # No retry: the LLM didn't render this text and can't fix it. Asking again only burns time.
        out.status = VS.UNVERIFIED
        out.failure_reason = _truncate(f"{v.reason} | rejected text: {text}")
        return out

    extra = _maybe_extra(llm, text, left)
    if extra is not None and not verifier.verify(text, extra, slot_json, [res], question, pj).ok:
        extra = None  # extra can never downgrade the status

    out.status, out.answer, out.extra = VS.VERIFIED, text, extra
    return out


def _finish_list(db, user_id, question, today, facts, call, out) -> Outcome:
    """A list_plan: check it, read the stored results, render, verify. No extra sentence: these answers contain
    merchant names from the CSV, and the extra-sentence call would send them to the LLM."""
    try:
        plan = ListPlan.model_validate_json(call.arguments)
    except ValidationError as e:
        raise _Retry(_short(e), call.id)
    try:
        plan_check.check_list_plan(facts, question)
    except plan_check.Unanswerable as u:
        return _fixed(out, u.label, source="code")

    res = TOOLS[LIST_TOOLS[plan.intent]](db, user_id, plan, today)
    render = templates.render_recurring_list if plan.intent == "recurring_list" else templates.render_flag_list
    text, slots = render(res)
    pj = list_plan_json(plan, res, today)
    slot_json = [asdict(s) for s in slots]
    out.tool_log.append({"phase": "final", "tool": "list_plan", "args": json.loads(call.arguments), "result": res})
    out.plan, out.slots, out.final_results = pj, slot_json, [res]

    v = verifier.verify(text, None, slot_json, [res], question, pj)
    if not v.ok:
        out.status = VS.UNVERIFIED
        out.failure_reason = _truncate(f"{v.reason} | rejected text: {text}")
        return out
    out.status, out.answer = VS.VERIFIED, text
    return out


def list_plan_json(plan: ListPlan, result: dict, today: date) -> dict:
    """The stored plan for a list answer: the intent, the 'today' it ran on, and the date the data runs through."""
    return {"intent": plan.intent, "as_of": today.isoformat(), "data_through": result["coverage"]["last_date"]}


def plan_json(plan: FinalPlan, resolved: ResolvedPeriod, today: date) -> dict:
    """The stored plan: the validated plan, the 'today' it was resolved against, and the resolved period."""
    return {**plan.model_dump(mode="json"), "as_of": today.isoformat(),
            "resolved": {"start": resolved.start.isoformat(), "end": resolved.end.isoformat(),
                         "partial": resolved.partial, "key": resolved.key}}


def extra_sentence_ok(s: str | None) -> bool:
    """One line, short, and free of digits, currency, number words and comparison/ranking claims."""
    return bool(s) and len(s) <= config.EXTRA_SENTENCE_MAX_CHARS and "\n" not in s \
        and not any(ch.isdigit() for ch in s) and not re.search(r"[$%€£]", s) \
        and not NUMBER_WORDS.search(s) and not CLAIM_WORDS.search(s)


def _maybe_extra(llm: LLM, text: str, left: Callable[[], float]) -> str | None:
    """Never raises: running out of time, an API error or bad content all just mean no extra sentence."""
    t = left()  # a plain float; NOT remaining(), which raises _Fail and would sink a verified answer
    if t < config.EXTRA_SENTENCE_MIN_TIME_S:
        return None
    try:
        reply = llm.complete([{"role": "system", "content": prompts.EXTRA_SENTENCE},
                              {"role": "user", "content": text}], None, "none", timeout=min(5.0, t - 0.5))
    except Exception:  # LLMError, or anything unexpected from the client
        return None
    s = (reply.text or "").strip()
    return s if extra_sentence_ok(s) else None


def _known_merchants(db: Session, user_id: int) -> frozenset[str]:
    """This user's distinct merchant names, lowercased, >= 3 characters. Read only by extract_facts."""
    names = db.scalars(select(Transaction.merchant).distinct()
                       .join(Account, Account.id == Transaction.account_id)
                       .where(Account.user_id == user_id, Transaction.merchant.is_not(None)))
    return frozenset(n.lower() for n in names if len(n) >= 3)


def _dedupe(cats):
    """Drop repeated categories, keeping first occurrences; "all_spending" passes through."""
    return cats if isinstance(cats, str) else list(dict.fromkeys(cats))


def _short(e: Exception) -> str:
    """A compact error for the LLM's retry message: Pydantic errors as 'loc: msg', capped at 300 characters."""
    if isinstance(e, ValidationError):
        msg = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:5])
    else:
        msg = str(e)
    return msg[:300]


def _assistant_msg(reply: LLMReply) -> dict:
    """The model's turn echoed back in OpenAI format, so every later role:tool message has its call."""
    return {"role": "assistant", "content": reply.text or None,
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.name, "arguments": c.arguments}} for c in reply.tool_calls]}


def _tool_msg(call_id: str, payload: dict) -> dict:
    """A role:tool message answering one call id."""
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}


def _raw_args(c: ToolCall) -> dict | str:
    """Arguments for the log: parsed JSON, or the raw string if the model sent invalid JSON."""
    try:
        return json.loads(c.arguments)
    except ValueError:
        return c.arguments


def _run_exploration(db, user_id, today, c: ToolCall) -> dict:
    """Run one exploration call. Bad arguments come back to the LLM as {"error": ...}; never raises."""
    try:
        args = TOOL_ARG_MODELS[c.name].model_validate_json(c.arguments)
        return TOOLS[c.name](db, user_id, args, today)
    except (ValidationError, PeriodError) as e:
        return {"error": _short(e)}
