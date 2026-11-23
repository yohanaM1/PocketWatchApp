"""POST /questions, GET /questions (history), GET /questions/{id}."""

import logging
import time
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app import config
from app.agent import replies
from app.agent.llm import LLM, get_llm
from app.agent.loop import Outcome, _truncate, answer_question
from app.agent.periods import now_et
from app.deps import CurrentUser, DbSession
from app.models import QueryLog, VerificationStatus

router = APIRouter(prefix="/questions", tags=["questions"])


class QuestionIn(BaseModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    question: str
    status: VerificationStatus = Field(validation_alias="verification_status")
    answer: str | None
    extra: str | None = Field(validation_alias="extra_sentence")  # UI labels this "not verified"
    plan: dict | None
    slots: list | None
    final_results: list | None
    attempts: int
    as_of: date | None
    created_at: datetime
    # No failure_reason: for `unverified` it holds the rejected text, whose numbers failed
    # verification. It stays in the database for audit and tests, and never goes to a client.


logger = logging.getLogger(__name__)
_recent: dict[int, deque[float]] = defaultdict(deque)  # per-user request times, for the rate limit


def rate_limit(user: CurrentUser) -> None:
    """At most AGENT_RATE_LIMIT_PER_MIN questions per user per minute (protects the course key).

    In-memory, so it holds only with a single worker process, which is the M2 setup.
    """
    now, q = time.monotonic(), _recent[user.id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= config.AGENT_RATE_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many questions; wait a minute")
    q.append(now)


@router.post("", response_model=QuestionOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(rate_limit)])
def ask(body: QuestionIn, user: CurrentUser, db: DbSession,
        llm: Annotated[LLM, Depends(get_llm)], now: Annotated[datetime, Depends(now_et)]) -> QueryLog:
    """Ask a spending question; returns a verified, unverified or failed answer (always 201; see `status`)."""
    log = QueryLog(user_id=user.id, question=body.question, verification_status=VerificationStatus.FAILED)
    db.add(log)
    db.commit()  # a crash below leaves a `failed` row, not nothing
    try:
        out = answer_question(db, user.id, body.question, llm, now.date())
    except Exception:  # last resort; never 500 for agent problems
        logger.exception("agent crashed on query %s", log.id)  # traceback in the server log (no question text)
        db.rollback()
        out = Outcome(status=VerificationStatus.FAILED, failure_reason="internal error")
    log.verification_status = out.status
    log.answer = out.answer if out.status is not VerificationStatus.FAILED else replies.FAILED_REPLY
    log.extra_sentence, log.plan, log.slots = out.extra, out.plan, out.slots
    log.final_results, log.tool_calls = out.final_results, out.tool_log
    log.failure_reason, log.attempts = _truncate(out.failure_reason), out.attempts
    log.llm_model, log.prompt_version = config.LLM_MODEL or None, config.PROMPT_VERSION
    log.as_of, log.latency_ms = now.date(), out.latency_ms
    db.commit()
    return log


@router.get("", response_model=list[QuestionOut])
def history(user: CurrentUser, db: DbSession, limit: int = Query(50, ge=1, le=200),
            before_id: int | None = None) -> list[QueryLog]:
    """Your past questions, newest first; pass the last id you saw as before_id for the next page. Never re-runs tools."""
    stmt = select(QueryLog).where(QueryLog.user_id == user.id)
    if before_id is not None:
        stmt = stmt.where(QueryLog.id < before_id)
    return list(db.scalars(stmt.order_by(QueryLog.created_at.desc(), QueryLog.id.desc()).limit(limit)))


@router.get("/{query_id}", response_model=QuestionOut)
def get_one(query_id: int, user: CurrentUser, db: DbSession) -> QueryLog:
    """One past question; 404 if it isn't yours."""
    q = db.scalar(select(QueryLog).where(QueryLog.id == query_id, QueryLog.user_id == user.id))
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Question not found")
    return q
