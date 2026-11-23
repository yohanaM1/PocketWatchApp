import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { listRecurring, recurringTransactions } from "../api/insights";
import type { RecurringPage as Page, RecurringSeries, TransactionOut } from "../api/types";
import { jewel } from "../lib/categories";
import { formatDate, formatMoney } from "./TransactionsPage";

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
/** "$8.99" from a negative amount string. */
const money = (amount: string) => formatMoney(amount.replace(/^-/, ""));

/** Today in the browser's time zone, as YYYY-MM-DD (compared as strings, like the API's dates). */
function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** "next Nov 18, 2026", or "expected Oct 21, 2026 (not imported yet)" once that date has passed. */
export function nextText(next: string, today: string = todayIso()): string {
  return next < today ? `expected ${formatDate(next)} (not imported yet)` : `next ${formatDate(next)}`;
}

export function RecurringPage() {
  const [page, setPage] = useState<Page | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [showStopped, setShowStopped] = useState(false);
  const request = useRef(0);

  const load = useCallback(async () => {
    const id = ++request.current;
    setStatus("loading");
    try {
      const next = await listRecurring();
      if (id !== request.current) return;
      setPage(next);
      setStatus("ready");
    } catch (err) {
      if (id === request.current && !isUnauthorized(err)) setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const active = page?.series.filter((s) => s.active) ?? [];
  const stopped = page?.series.filter((s) => !s.active) ?? [];

  return (
    <main className="page">
      <header className="page-header">
        <h1>Recurring</h1>
        <p className="caption">
          Charges that repeat every month from the same merchant, found automatically after every import.
        </p>
      </header>

      {status === "error" ? (
        <div className="card notice" role="alert">
          <p>Couldn't load your recurring payments.</p>
          <button type="button" onClick={() => void load()}>Retry</button>
        </div>
      ) : status === "loading" || page === null ? (
        <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading recurring payments…</p>
      ) : page.series.length === 0 ? (
        <div className="card stack">
          <h2>No recurring payments yet</h2>
          <p className="caption">
            They appear after two monthly charges from the same merchant.{" "}
            {page.as_of === null ? <Link to="/transactions">Import a CSV</Link> : <Link to="/transactions">Import more statements</Link>}
            {" "}to find them.
          </p>
        </div>
      ) : (
        <>
          {page.as_of && <p className="caption">Based on transactions through {formatDate(page.as_of)}.</p>}
          <section className="tiles recurring-tiles" aria-label="Recurring total">
            <div className="tile" style={{ "--tile": "var(--jewel-amethyst)" } as CSSProperties}>
              <span className="tile-label">Every month</span>
              <span className="tile-value">{money(page.monthly_total)}</span>
              <span className="caption">across {plural(page.active_count, "active payment", "active payments")}</span>
            </div>
          </section>

          {active.length > 0 ? (
            <ul className="card recurring-list" aria-label="Active recurring payments">
              {active.map((s) => <SeriesRow key={s.key} series={s} onGone={() => void load()} />)}
            </ul>
          ) : (
            <p className="card caption">None of your recurring payments are active right now.</p>
          )}

          {stopped.length > 0 && (
            <section className="stack" aria-label="Stopped recurring payments">
              <button type="button" className="link" aria-expanded={showStopped} onClick={() => setShowStopped((x) => !x)}>
                {showStopped ? "Hide" : "Show"} {plural(stopped.length, "stopped payment", "stopped payments")}
              </button>
              {showStopped && (
                <ul className="card recurring-list stopped">
                  {stopped.map((s) => <SeriesRow key={s.key} series={s} onGone={() => void load()} />)}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}

function SeriesRow({ series: s, onGone }: { series: RecurringSeries; onGone: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [rows, setRows] = useState<TransactionOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rowsId = `charges-${s.key.replace(/[^A-Za-z0-9]+/g, "-")}`;

  useEffect(() => {
    if (!expanded || rows !== null) return;
    let live = true;
    recurringTransactions(s.key)
      .then((r) => { if (live) setRows(r); })
      .catch((err) => {
        if (!live || isUnauthorized(err)) return;
        setError(err instanceof ApiError && err.status === 404
          ? "This list changed. Refresh to see the latest."
          : "Couldn't load these charges. Try again.");
      });
    return () => { live = false; };
  }, [expanded, rows, s.key]);

  return (
    <li className="recurring-row">
      <div className="recurring-main">
        <span className="recurring-merchant">{s.merchant}</span>
        {s.category && (
          <span className="chip" style={{ "--chip": jewel(s.category) } as CSSProperties}>{s.category}</span>
        )}
      </div>
      <div className="recurring-amount">
        <span className="amount">{money(s.amount)}</span>
        <span className="caption">a month</span>
      </div>
      <p className="caption recurring-detail">
        {s.previous_amount && s.amount_changed_on && (
          <>was {money(s.previous_amount)} until {formatDate(s.amount_changed_on)} · </>
        )}
        every ~{s.interval_days} days · last charged {formatDate(s.last_date)}
        {s.next_expected && <> · {nextText(s.next_expected)}</>}
      </p>
      <div className="recurring-foot">
        <button type="button" className="link" aria-expanded={expanded} aria-controls={rowsId}
          onClick={() => setExpanded((x) => !x)}>
          {expanded ? "Hide charges" : `Show ${plural(s.charge_count, "charge", "charges")}`}
        </button>
        {error && (
          <p className="error" role="alert">
            {error}
            {error.startsWith("This list changed") && (
              <> <button type="button" className="link" onClick={onGone}>Refresh</button></>
            )}
          </p>
        )}
      </div>
      {expanded && rows !== null && (
        <ul id={rowsId} className="review-rows" aria-label={`${s.merchant} charges`}>
          {rows.map((t) => (
            <li key={t.id}>
              <span className="nowrap">{formatDate(t.date)}</span>
              <span className="review-desc">{t.description}</span>
              <span className="amount">{money(t.amount)}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
