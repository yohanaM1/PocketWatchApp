import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from "react";

import { ApiError } from "../api/client";
import { changeRule, listRules, removeRule } from "../api/corrections";
import type { SavedRule } from "../api/types";
import { jewel } from "../lib/categories";
import { notifyReviewChanged, onReviewChanged } from "../lib/reviewEvents";

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const FLASH_MS = 4000;

/** The saved rules list on the Review page: see, change or remove the category corrections future imports use.
 * `onSentToReview` runs after a removal sends transactions back to the queue, so the page can reload it. */
export function SavedRules({ categories, onSentToReview }: { categories: string[]; onSentToReview: () => void }) {
  const [rules, setRules] = useState<SavedRule[] | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [shown, setShown] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const request = useRef(0);
  const listId = useId();

  const load = useCallback(async (quiet = false) => {
    const id = ++request.current;
    if (!quiet) setStatus("loading");
    try {
      const next = await listRules();
      if (id !== request.current) return;
      setRules(next);
      setStatus("ready");
    } catch (err) {
      if (id === request.current && !isUnauthorized(err) && !quiet) setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
    // Resolving a merchant with Remember on saves a rule: show it without a reload.
    return onReviewChanged(() => void load(true));
  }, [load]);

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), FLASH_MS);
    return () => clearTimeout(t);
  }, [notice]);

  function replaced(rule: SavedRule) {
    setRules((prev) => prev && prev.map((r) => (r.id === rule.id ? rule : r)));
  }

  function removed(rule: SavedRule, sent: number) {
    setRules((prev) => prev && prev.filter((r) => r.id !== rule.id));
    setNotice(sent > 0
      ? `Removed the ${rule.merchant} rule · ${plural(sent, "transaction", "transactions")} sent back to review.`
      : `Removed the ${rule.merchant} rule. Its transactions keep their category.`);
    if (sent > 0) onSentToReview();
    notifyReviewChanged();
  }

  return (
    <section className="saved-rules stack" aria-labelledby={`${listId}-title`}>
      <div className="saved-rules-head">
        <h2 id={`${listId}-title`}>Saved rules{rules && rules.length > 0 ? ` (${rules.length})` : ""}</h2>
        <button type="button" className="link" aria-expanded={shown} aria-controls={listId}
          onClick={() => setShown((x) => !x)}>
          {shown ? "Hide" : "Show"}
        </button>
      </div>
      {shown && (
        <div id={listId} className="stack">
          <p className="caption">
            These categorize future imports from a merchant automatically. Change or remove one that's wrong.
          </p>
          {notice && <p className="card success saved-rules-notice" role="status">{notice}</p>}
          {status === "error" ? (
            <div className="card notice" role="alert">
              <p>Couldn't load your saved rules.</p>
              <button type="button" onClick={() => void load()}>Retry</button>
            </div>
          ) : status === "loading" || rules === null ? (
            <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading saved rules…</p>
          ) : rules.length === 0 ? (
            <p className="card caption">No saved rules yet. Choosing Remember when you review a merchant saves one.</p>
          ) : (
            <ul className="card saved-rules-list" aria-label="Saved rules">
              {rules.map((r) => (
                <RuleRow key={r.id} rule={r} categories={categories} onReplaced={replaced} onRemoved={removed} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function RuleRow({ rule: r, categories, onReplaced, onRemoved }: {
  rule: SavedRule; categories: string[]; onReplaced: (rule: SavedRule) => void;
  onRemoved: (rule: SavedRule, sent: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<string | null>(null); // a new category waiting for confirmation
  const [confirming, setConfirming] = useState(false);
  const [sendBack, setSendBack] = useState(false); // Keep is the default
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const radio = useId();

  useEffect(() => {
    if (!message) return;
    const t = setTimeout(() => setMessage(null), FLASH_MS);
    return () => clearTimeout(t);
  }, [message]);

  const gone = (err: unknown) => err instanceof ApiError && err.status === 404;

  /** Picking a category only asks; nothing moves until Change is pressed. */
  function pick(category: string) {
    setError(null);
    setConfirming(false);
    setPending(category === r.category ? null : category);
  }

  async function change(category: string) {
    setBusy(true);
    setError(null);
    try {
      const res = await changeRule(r.id, category);
      setPending(null);
      onReplaced(res.rule);
      setMessage(res.updated > 0 ? `Updated ${plural(res.updated, "transaction", "transactions")}` : "Saved");
      notifyReviewChanged(); // categories changed, so flags can too
    } catch (err) {
      if (!isUnauthorized(err)) setError(gone(err) ? "This rule was already removed. Reload the page." : "Couldn't change this rule. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const res = await removeRule(r.id, sendBack);
      onRemoved(r, res.sent_to_review);
    } catch (err) {
      if (!isUnauthorized(err)) setError(gone(err) ? "This rule was already removed. Reload the page." : "Couldn't remove this rule. Try again.");
      setBusy(false);
    }
  }

  const count = plural(r.transaction_count, "transaction", "transactions");
  return (
    <li className="saved-rule">
      <div className="saved-rule-main">
        <span className="saved-rule-merchant">{r.merchant}</span>
        <select className="cat-select" aria-label={`Category for ${r.merchant}`} value={pending ?? r.category}
          disabled={busy} style={{ "--chip": jewel(pending ?? r.category) } as CSSProperties}
          onChange={(e) => pick(e.target.value)}>
          {categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <span className="caption">{count}</span>
        {r.mixed && <span className="saved-rule-mixed">Mixed merchant</span>}
        {message && <span className="caption saved-rule-flash" role="status">{message}</span>}
        {!confirming && pending === null && (
          <button type="button" className="link saved-rule-remove" disabled={busy}
            aria-label={`Remove the ${r.merchant} rule`} onClick={() => setConfirming(true)}>
            Remove
          </button>
        )}
      </div>
      {pending !== null && (
        <div className="saved-rule-confirm" role="group" aria-label={`Change the ${r.merchant} rule?`}>
          <p className="saved-rule-question">
            Change the {r.merchant} rule from <strong>{r.category}</strong> to <strong>{pending}</strong>?
          </p>
          <p className="caption">
            {r.transaction_count > 0
              ? `This moves its ${count} from ${r.category} to ${pending}, and future ${r.merchant} imports go to ${pending}.`
              : `Future ${r.merchant} imports will go to ${pending}.`}
          </p>
          <div className="row-actions">
            <button type="button" disabled={busy} onClick={() => void change(pending)}>{busy ? "Changing…" : "Change"}</button>
            <button type="button" className="link" disabled={busy} onClick={() => pick(r.category)}>Cancel</button>
          </div>
        </div>
      )}
      {confirming && (
        <fieldset className="saved-rule-confirm" disabled={busy}>
          <legend>Remove the {r.merchant} → {r.category} rule?</legend>
          <label>
            <input type="radio" name={radio} checked={!sendBack} onChange={() => setSendBack(false)} />
            Keep its {count} as they are
          </label>
          <label>
            <input type="radio" name={radio} checked={sendBack} onChange={() => setSendBack(true)} />
            Send its {count} back to review
          </label>
          <p className="caption">Either way, future imports from {r.merchant} won't use this rule.</p>
          <div className="row-actions">
            <button type="button" onClick={() => void remove()}>{busy ? "Removing…" : "Remove"}</button>
            <button type="button" className="link" onClick={() => { setConfirming(false); setSendBack(false); }}>Cancel</button>
          </div>
        </fieldset>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  );
}
