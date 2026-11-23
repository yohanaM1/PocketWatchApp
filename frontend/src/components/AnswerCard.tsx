import { useState } from "react";

import type { QuestionOut } from "../api/types";
import { AnswerText } from "./AnswerText";
import { SourcePanel } from "./SourcePanel";

/** True for a fixed reply (greeting, unsupported, future period): no facts, so no badge. */
export function isFixedReply(a: QuestionOut): boolean {
  return a.status === "verified" && a.plan?.intent === "no_data";
}

export function StatusBadge({ answer }: { answer: QuestionOut }) {
  if (isFixedReply(answer)) return <span className="badge plain">Reply</span>;
  if (answer.status === "verified") return <span className="badge verified">Verified</span>;
  if (answer.status === "unverified") return <span className="badge unverified">Couldn't verify</span>;
  return <span className="badge failed">No answer</span>;
}

/** One presentation per status. Every API string is rendered as a text node, never HTML. */
export function AnswerCard({ answer, onRetry }: { answer: QuestionOut; onRetry: () => void }) {
  const [showSources, setShowSources] = useState(false);

  if (isFixedReply(answer)) {
    return <article className="card"><p className="answer-text">{answer.answer}</p></article>;
  }

  if (answer.status === "verified") {
    return (
      <article className="card">
        <header className="card-head">
          <StatusBadge answer={answer} />
          <span className="caption">Every number here was checked against your transactions.</span>
        </header>
        <AnswerText text={answer.answer ?? ""} slots={answer.slots} plan={answer.plan} results={answer.final_results} />
        {answer.extra && (
          <aside className="extra">
            <p className="extra-label">AI comment · not verified</p>
            <p>{answer.extra}</p>
          </aside>
        )}
        <button type="button" className="link" aria-expanded={showSources} onClick={() => setShowSources(!showSources)}>
          {showSources ? "Hide sources" : "Show sources"}
        </button>
        {showSources && <SourcePanel plan={answer.plan} results={answer.final_results} />}
      </article>
    );
  }

  if (answer.status === "unverified") {
    return (
      <article className="card">
        <header className="card-head"><StatusBadge answer={answer} /></header>
        <p>We couldn't verify the written answer, so here are the numbers straight from your transactions.</p>
        <SourcePanel plan={answer.plan} results={answer.final_results} />
      </article>
    );
  }

  return (
    <article className="card">
      <header className="card-head"><StatusBadge answer={answer} /></header>
      <p>{answer.answer}</p>
      <button type="button" onClick={onRetry}>Try again</button>
    </article>
  );
}
