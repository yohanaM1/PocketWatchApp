import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { listReview, resolveGroup } from "../api/review";
import { listCategories, listTransactions, setCategory } from "../api/transactions";
import { SavedRules } from "../components/SavedRules";
import type { ReviewGroup, TransactionOut } from "../api/types";
import { jewel } from "../lib/categories";
import { notifyReviewChanged } from "../lib/reviewEvents";
import { formatAmount, formatDate, formatMoney } from "./TransactionsPage";

const PAGE = 20;
const MAX_ROWS = 200; // the backend's limit cap; a group bigger than this shows its first 200 rows

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const keyOf = (g: ReviewGroup) => g.merchant ?? "\u0000";

/** "Guess: Dining · 60% sure", "AI guesses differ: Dining ×5, Groceries ×2" or "No category". */
export function reasonText(g: ReviewGroup): string {
  if (g.guesses.length === 0) return "No category";
  if (g.guesses.length === 1) {
    const { category, confidence } = g.guesses[0];
    return confidence === null ? `Guess: ${category}` : `Guess: ${category} · ${Math.round(confidence * 100)}% sure`;
  }
  return `AI guesses differ: ${g.guesses.map((x) => `${x.category} ×${x.count}`).join(", ")}`;
}

function dateRange(g: ReviewGroup): string {
  return g.first_date === g.last_date ? formatDate(g.first_date) : `${formatDate(g.first_date)} – ${formatDate(g.last_date)}`;
}

export function ReviewPage() {
  const [groups, setGroups] = useState<ReviewGroup[]>([]);
  const [totals, setTotals] = useState({ groups: 0, transactions: 0 });
  const [categories, setCategories] = useState<string[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [done, setDone] = useState<Record<string, string>>({}); // resolved groups -> their confirmation
  const [hasTransactions, setHasTransactions] = useState(true);
  const request = useRef(0);

  useEffect(() => {
    listCategories().then(setCategories).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    const id = ++request.current;
    setStatus("loading");
    try {
      const page = await listReview({ limit: PAGE, offset: 0 });
      if (id !== request.current) return;
      setGroups(page.groups);
      setTotals({ groups: page.total_groups, transactions: page.total_transactions });
      setDone({});
      setStatus("ready");
      if (page.total_transactions === 0) {
        const any = await listTransactions({}, { limit: 1, offset: 0 });
        if (id === request.current) setHasTransactions(any.total > 0);
      }
    } catch (err) {
      if (id === request.current && !isUnauthorized(err)) setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const open = groups.filter((g) => !(keyOf(g) in done));

  async function loadMore() {
    const id = request.current;
    setLoadingMore(true);
    try {
      // Resolved groups have left the server's list, so the next page starts after the ones still open.
      const page = await listReview({ limit: PAGE, offset: open.length });
      if (id !== request.current) return;
      const seen = new Set(groups.map(keyOf));
      setGroups((prev) => [...prev, ...page.groups.filter((g) => !seen.has(keyOf(g)))]);
      setTotals({ groups: page.total_groups, transactions: page.total_transactions });
    } catch {
      // keep what we have; the button stays
    } finally {
      setLoadingMore(false);
    }
  }

  /** A card finished `count` of its rows; `message` set means the whole group is done. */
  function onProgress(g: ReviewGroup, count: number, message: string | null) {
    setTotals((t) => ({ groups: t.groups - (message ? 1 : 0), transactions: Math.max(t.transactions - count, 0) }));
    if (message) setDone((prev) => ({ ...prev, [keyOf(g)]: message }));
    notifyReviewChanged();
  }

  const caughtUp = status === "ready" && open.length === 0 && totals.transactions === 0;

  return (
    <main className="page">
      <header className="page-header">
        <h1>Review</h1>
        <p className="caption">
          Transactions PocketWatch couldn't categorize, or wasn't sure about. Your choices can be remembered for future imports.
        </p>
      </header>

      {status === "error" ? (
        <div className="card notice" role="alert">
          <p>Couldn't load the review queue.</p>
          <button type="button" onClick={() => void load()}>Retry</button>
        </div>
      ) : status === "loading" ? (
        <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading the review queue…</p>
      ) : (
        <>
          {totals.transactions > 0 && (
            <p className="review-summary">
              <strong>{plural(totals.transactions, "transaction", "transactions")}</strong> from{" "}
              <strong>{plural(totals.groups, "merchant", "merchants")}</strong> need a category.
            </p>
          )}
          {groups.map((g) =>
            keyOf(g) in done ? (
              <p key={keyOf(g)} className="card success review-done" role="status">{done[keyOf(g)]}</p>
            ) : (
              <GroupCard key={keyOf(g)} group={g} categories={categories} onProgress={(n, m) => onProgress(g, n, m)} />
            ),
          )}
          {caughtUp && (
            <div className="card stack review-empty">
              <h2>All caught up</h2>
              {hasTransactions ? (
                <p className="caption">Every transaction has a category. <Link to="/transactions">See your transactions</Link>.</p>
              ) : (
                <p className="caption">No transactions yet. <Link to="/transactions">Import a CSV</Link> to get started.</p>
              )}
            </div>
          )}
          {open.length < totals.groups && (
            <button type="button" className="link" disabled={loadingMore} onClick={() => void loadMore()}>
              {loadingMore ? "Loading…" : "Show more merchants"}
            </button>
          )}
        </>
      )}

      {/* A removed rule can send its transactions back to the queue above, so reload it then. */}
      <SavedRules categories={categories} onSentToReview={() => void load()} />
    </main>
  );
}

function GroupCard({ group: g, categories, onProgress }: {
  group: ReviewGroup;
  categories: string[];
  onProgress: (resolvedRows: number, doneMessage: string | null) => void;
}) {
  const name = g.merchant ?? g.examples[0]?.description ?? "Unknown merchant";
  const [choice, setChoice] = useState(g.suggestion ?? "");
  const [remember, setRemember] = useState(!g.mixed);
  const [expanded, setExpanded] = useState(g.mixed);
  const [rows, setRows] = useState<TransactionOut[] | null>(null);
  const [remaining, setRemaining] = useState(g.count);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const titleId = `review-${keyOf(g).replace(/[^A-Za-z0-9]+/g, "-")}`;

  useEffect(() => {
    if (!expanded || rows !== null) return;
    let live = true;
    listTransactions({ merchant: g.merchant ?? undefined, review: true }, { limit: MAX_ROWS, offset: 0 })
      .then((page) => { if (live) setRows(page.items); })
      .catch((err) => { if (live && !isUnauthorized(err)) setError("Couldn't load these transactions. Try again."); });
    return () => { live = false; };
  }, [expanded, rows, g.merchant]);

  async function applyAll() {
    setBusy(true);
    setError(null);
    try {
      const res = await resolveGroup(g.merchant, choice, remember);
      let message = `Set ${plural(res.updated, `${name} transaction`, `${name} transactions`)} to ${choice}`;
      if (res.also_updated > 0) message += ` · also updated ${res.also_updated}`;
      if (remember) message += " · remembered for future imports";
      onProgress(res.updated, `${message}.`);
    } catch (err) {
      if (!isUnauthorized(err)) {
        setError(err instanceof ApiError && err.status === 404
          ? "These transactions were already reviewed. Reload to see what's left."
          : "Couldn't save the category. Try again.");
      }
      setBusy(false);
    }
  }

  async function setOne(t: TransactionOut, category: string) {
    setBusy(true);
    setError(null);
    try {
      await setCategory(t.id, category);
      const left = remaining - 1;
      setRows((prev) => prev && prev.filter((x) => x.id !== t.id));
      setRemaining(left);
      onProgress(1, left === 0 ? `Reviewed every ${name} transaction one by one.` : null);
    } catch (err) {
      if (!isUnauthorized(err)) setError("Couldn't save the category. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const applyButton = (
    <button type="button" className={g.mixed ? "secondary" : undefined} disabled={busy || !choice}
      onClick={() => void applyAll()}>
      Apply to all {remaining}
    </button>
  );

  return (
    <article className="card stack review-card" aria-labelledby={titleId} aria-busy={busy}>
      <div className="review-head">
        <div>
          <h2 id={titleId} className="review-merchant">{name}</h2>
          <p className="caption">
            {plural(remaining, "transaction", "transactions")} · {dateRange(g)} · {formatMoney(g.money_out)} out
            {g.money_in !== "0.00" && <> · {formatMoney(g.money_in)} in</>}
          </p>
        </div>
        <span className="chip" style={{ "--chip": jewel(g.guesses.length === 1 ? g.guesses[0].category : null) } as CSSProperties}>
          {reasonText(g)}
        </span>
      </div>

      {g.mixed && (
        <p className="mixed-note">
          <strong>Mixed merchant:</strong> usually needs a category per purchase.
          {g.mixed_reason === "guesses_differ" && " The AI's guesses for it disagree."}
          {g.mixed_reason === "history_differs" && " You've given it different categories before."}
        </p>
      )}

      {expanded && (
        rows === null ? (
          !error && <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading transactions…</p>
        ) : (
          <ul className="review-rows" aria-label={`${name} transactions`}>
            {rows.map((t) => (
              <li key={t.id}>
                <span className="nowrap">{formatDate(t.date)}</span>
                <span className="review-desc">{t.description}</span>
                <span className={t.amount.startsWith("-") ? "amount" : "amount money-in"}>{formatAmount(t.amount)}</span>
                <select className={t.category ? "cat-select" : "cat-select empty"} style={{ "--chip": jewel(t.category) } as CSSProperties}
                  aria-label={`Category for ${name} on ${formatDate(t.date)}`} value={t.category ?? ""} disabled={busy}
                  onChange={(e) => void setOne(t, e.target.value)}>
                  {t.category === null && <option value="" disabled>Choose…</option>}
                  {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </li>
            ))}
          </ul>
        )
      )}

      <div className="review-actions">
        <label>
          {g.mixed ? "Or set all to" : "Category"}
          <select className={choice ? "cat-select" : "cat-select empty"} style={{ "--chip": jewel(choice || null) } as CSSProperties}
            value={choice} disabled={busy} onChange={(e) => setChoice(e.target.value)}>
            {!choice && <option value="" disabled>Choose…</option>}
            {categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={remember} disabled={busy} onChange={(e) => setRemember(e.target.checked)} />
          Remember for future imports
        </label>
        {applyButton}
      </div>

      <div className="review-foot">
        <button type="button" className="link" onClick={() => setExpanded((x) => !x)}>
          {expanded ? "Hide transactions" : "Show transactions"}
        </button>
        {error && <p className="error" role="alert">{error}</p>}
      </div>
    </article>
  );
}
