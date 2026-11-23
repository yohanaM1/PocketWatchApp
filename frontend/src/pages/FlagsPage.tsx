import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { listFlags, setFlagStatus } from "../api/insights";
import type { Flag, FlagKind, FlagStatus } from "../api/types";
import { notifyFlagsChanged } from "../lib/flagEvents";
import { formatAmount, formatDate } from "./TransactionsPage";

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out

export const KIND_LABELS: Record<FlagKind, string> = {
  duplicate: "Possible duplicate",
  large_charge: "Unusually large",
  unfamiliar_merchant: "New merchant",
};
const KIND_COLORS: Record<FlagKind, string> = {
  duplicate: "var(--jewel-topaz)",
  large_charge: "var(--jewel-ruby)",
  unfamiliar_merchant: "var(--jewel-sapphire)",
};
const FILTERS: { value: FlagStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "dismissed", label: "Dismissed" },
  { value: "confirmed", label: "Confirmed" },
];
const EMPTY: Record<FlagStatus, string> = {
  open: "Nothing looks unusual right now.",
  dismissed: "You haven't dismissed any flags.",
  confirmed: "You haven't confirmed any flags.",
};
const DONE: Record<FlagStatus, string> = {
  open: "Reopened.",
  dismissed: "Dismissed: marked as looking fine.",
  confirmed: "Confirmed: marked as not right.",
};

export function FlagsPage() {
  const [filter, setFilter] = useState<FlagStatus>("open");
  const [flags, setFlags] = useState<Flag[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [moved, setMoved] = useState<Record<number, FlagStatus>>({}); // flags acted on in this view -> new status
  const request = useRef(0);

  const load = useCallback(async (which: FlagStatus) => {
    const id = ++request.current;
    setStatus("loading");
    try {
      const page = await listFlags(which);
      if (id !== request.current) return;
      setFlags(page.items);
      setMoved({});
      setStatus("ready");
    } catch (err) {
      if (id === request.current && !isUnauthorized(err)) setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load(filter);
  }, [load, filter]);

  const remaining = flags.filter((f) => !(f.id in moved));

  return (
    <main className="page">
      <header className="page-header">
        <h1>Flags</h1>
        <p className="caption">
          Possible duplicates, unusually large charges and first charges from new merchants, found after every import.
        </p>
      </header>

      <div className="segmented" role="group" aria-label="Show flags">
        {FILTERS.map((f) => (
          <button key={f.value} type="button" className={filter === f.value ? "active" : undefined}
            aria-pressed={filter === f.value} onClick={() => setFilter(f.value)}>
            {f.label}
          </button>
        ))}
      </div>

      {status === "error" ? (
        <div className="card notice" role="alert">
          <p>Couldn't load your flags.</p>
          <button type="button" onClick={() => void load(filter)}>Retry</button>
        </div>
      ) : status === "loading" ? (
        <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading flags…</p>
      ) : (
        <>
          {flags.map((f) =>
            f.id in moved ? (
              <p key={f.id} className="card success review-done" role="status">{DONE[moved[f.id]]}</p>
            ) : (
              <FlagCard key={f.id} flag={f}
                onMoved={(next) => { setMoved((m) => ({ ...m, [f.id]: next })); notifyFlagsChanged(); }} />
            ),
          )}
          {remaining.length === 0 && (
            <div className="card stack review-empty">
              <h2>{filter === "open" ? "All clear" : "Nothing here"}</h2>
              <p className="caption">
                {EMPTY[filter]}
                {filter === "open" && <> Flags appear after you <Link to="/transactions">import transactions</Link>.</>}
              </p>
            </div>
          )}
        </>
      )}
    </main>
  );
}

function FlagCard({ flag: f, onMoved }: { flag: Flag; onMoved: (next: FlagStatus) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const t = f.transaction;
  const name = t.merchant ?? t.description;
  const titleId = `flag-${f.id}`;

  async function move(next: FlagStatus) {
    setBusy(true);
    setError(null);
    try {
      await setFlagStatus(f.id, next);
      onMoved(next);
    } catch (err) {
      if (!isUnauthorized(err)) {
        setError(err instanceof ApiError && err.status === 404
          ? "This flag is gone. Reload to see the latest."
          : "Couldn't save that. Try again.");
      }
      setBusy(false);
    }
  }

  return (
    <article className="card stack flag-card" aria-labelledby={titleId} aria-busy={busy}>
      <div className="review-head">
        <div>
          <h2 id={titleId} className="review-merchant">{name}</h2>
          <p className="caption">
            {formatDate(t.date)} · {t.account_name}{t.category && <> · {t.category}</>}
          </p>
        </div>
        <div className="flag-side">
          <span className="chip" style={{ "--chip": KIND_COLORS[f.kind] } as CSSProperties}>{KIND_LABELS[f.kind]}</span>
          <span className={t.amount.startsWith("-") ? "amount" : "amount money-in"}>{formatAmount(t.amount)}</span>
        </div>
      </div>
      <p className="flag-reason">{f.reason}</p>
      <p className="caption flag-desc">{t.description}</p>
      <div className="review-actions">
        {f.status === "open" ? (
          <>
            <button type="button" disabled={busy} onClick={() => void move("dismissed")}>Looks fine</button>
            <button type="button" className="secondary" disabled={busy} onClick={() => void move("confirmed")}>
              Not right
            </button>
          </>
        ) : (
          <button type="button" className="secondary" disabled={busy} onClick={() => void move("open")}>Reopen</button>
        )}
        {error && <p className="error" role="alert">{error}</p>}
      </div>
      {f.status === "open" && (
        <p className="caption">"Not right" only records your answer. Totals don't change.</p>
      )}
    </article>
  );
}
