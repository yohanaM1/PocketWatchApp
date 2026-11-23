import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { ask, history as fetchHistory } from "../api/questions";
import type { QuestionOut } from "../api/types";
import { AnswerCard, StatusBadge } from "../components/AnswerCard";

export const ASK_TIMEOUT_MS = 30_000; // the server's 20 s deadline plus the extra sentence and the network
const PAGE = 20;
const MAX_CHARS = 500;
const EXAMPLES = ["How much did I spend on dining in September?", "What did I spend on food last month?"];

type Notice = { kind: "rate_limited" } | { kind: "invalid" } | { kind: "lost" } | { kind: "error" };

const NOTICE_TEXT: Record<Notice["kind"], string> = {
  rate_limited: "You've asked a lot of questions in the last minute. Wait a moment and try again.",
  invalid: "Please enter a question of up to five hundred characters.",
  lost: "We lost the connection. Your question may still appear in history shortly.",
  error: "Something went wrong. Please try again.",
};

function when(created: string): string {
  const d = new Date(created);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("en-US", { month: "short", day: "numeric" });
}

export function AskPage() {
  const [question, setQuestion] = useState("");
  const [current, setCurrent] = useState<QuestionOut | null>(null);
  const [waiting, setWaiting] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [items, setItems] = useState<QuestionOut[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  async function loadHistory() {
    try {
      const page = await fetchHistory({ limit: PAGE });
      setItems(page);
      setHasMore(page.length === PAGE);
    } catch {
      // history is optional for asking; a 401 is handled by the client
    }
  }

  useEffect(() => {
    void loadHistory();
  }, []);

  async function loadOlder() {
    const last = items[items.length - 1];
    if (!last) return;
    try {
      const page = await fetchHistory({ limit: PAGE, beforeId: last.id });
      setItems((prev) => [...prev, ...page]);
      setHasMore(page.length === PAGE);
    } catch {
      // keep what we have
    }
  }

  function show(answer: QuestionOut) {
    setCurrent(answer);
    setItems((prev) => [answer, ...prev.filter((x) => x.id !== answer.id)]);
  }

  async function submit(text: string) {
    const q = text.trim();
    if (!q || q.length > MAX_CHARS || waiting) return;
    const lastSeenId = itemsRef.current.reduce((m, x) => Math.max(m, x.id), 0);
    setWaiting(true);
    setNotice(null);
    setCurrent(null);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ASK_TIMEOUT_MS);
    try {
      show(await ask(q, controller.signal));
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 429) setNotice({ kind: "rate_limited" });
        else if (err.status === 422) setNotice({ kind: "invalid" });
        else if (err.status !== 401) setNotice({ kind: "error" });
      } else {
        // Timeout or network error: the server may still have stored the answer.
        await recover(q, lastSeenId);
      }
    } finally {
      clearTimeout(timer);
      setWaiting(false);
    }
  }

  async function recover(q: string, lastSeenId: number) {
    try {
      const recent = await fetchHistory({ limit: 5 });
      const match = recent.find((x) => x.id > lastSeenId && x.question === q);
      if (match) {
        show(match);
        return;
      }
    } catch {
      // fall through to the lost-connection message
    }
    setNotice({ kind: "lost" });
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void submit(question);
  }

  return (
    <main className="page">
      <header className="page-header">
        <h1>Ask</h1>
        <p className="caption">Plain-English questions about your spending, answered from your own transactions.</p>
      </header>

      <form className="card stack" onSubmit={onSubmit}>
        <label htmlFor="question"><h2>Ask about your spending</h2></label>
        <div className="ask-row">
          <input id="question" value={question} maxLength={MAX_CHARS} disabled={waiting} autoComplete="off"
            placeholder={EXAMPLES[0]} onChange={(e) => setQuestion(e.target.value)} />
          <button type="submit" disabled={waiting || !question.trim()}>Ask</button>
        </div>
        <p className="examples">
          e.g.{" "}
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="link" disabled={waiting} onClick={() => setQuestion(ex)}>
              “{ex}”
            </button>
          ))}
        </p>
      </form>

      <section aria-live="polite" className="current">
        {waiting && (
          <div className="card waiting" role="status">
            <span className="spinner" aria-hidden="true" /> Checking your transactions… this can take up to 20 seconds.
          </div>
        )}
        {notice && (
          <div className="card notice">
            <p>{NOTICE_TEXT[notice.kind]}</p>
            {notice.kind === "lost" && <button type="button" onClick={() => void loadHistory()}>Refresh history</button>}
          </div>
        )}
        {current && !waiting && <AnswerCard key={current.id} answer={current} onRetry={() => void submit(current.question)} />}
      </section>

      <section className="history" aria-label="History">
        <h2>History</h2>
        {items.length === 0 ? (
          <p className="caption">Your questions will appear here.</p>
        ) : (
          <ul>
            {items.map((x) => (
              <li key={x.id}>
                <button type="button" className="history-item" onClick={() => { setNotice(null); setCurrent(x); }}>
                  <span className="history-q">{x.question}</span>
                  <StatusBadge answer={x} />
                  <span className="caption">{when(x.created_at)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {hasMore && <button type="button" className="link" onClick={() => void loadOlder()}>Show older</button>}
      </section>
    </main>
  );
}
