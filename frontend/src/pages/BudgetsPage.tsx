import {
  Fragment, useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode,
} from "react";
import { Link } from "react-router-dom";

import { copyBudgets, deleteBudget, getBudgets, setBudget } from "../api/budgets";
import { ApiError } from "../api/client";
import { listTransactions } from "../api/transactions";
import type { BudgetPage as Page, BudgetRow, BudgetStatus, TransactionOut } from "../api/types";
import { jewel } from "../lib/categories";
import { formatDate, formatMoney } from "./TransactionsPage";

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const SAVED_MS = 2000;
const UNDO_MS = 6000;
const DETAIL_PURCHASES = 8;

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

/** "October 2026" from "2026-10". */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTHS[m - 1]} ${y}`;
}

/** "October" from "2026-10". */
const monthName = (month: string) => MONTHS[Number(month.slice(5)) - 1];

/** "2026-10" moved by n months: shiftMonth("2026-01", -1) is "2025-12". */
export function shiftMonth(month: string, n: number): string {
  const [y, m] = month.split("-").map(Number);
  const i = y * 12 + (m - 1) + n;
  return `${Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`;
}

/** "$31.75" for any sign (callers say "over"). */
const money = (amount: string) => formatMoney(amount.replace(/^-/, ""));

/** "150", "$1,250.50" -> "150", "1250.50". */
const cleanLimit = (value: string) => value.trim().replace(/^\$/, "").replace(/,/g, "");

/** null for a valid limit, else what to tell the user. Matches the API: > 0, at most 1,000,000, 2 decimals. */
export function limitError(value: string): string | null {
  const v = cleanLimit(value);
  if (!/^\d+(\.\d{1,2})?$/.test(v) || Number(v) <= 0) return "Enter an amount above $0, like 150 or 150.00.";
  if (Number(v) > 1_000_000) return "Enter an amount up to $1,000,000.";
  return null;
}

/** "+$20.67", "−$56.23", "same", or "—" when last month wasn't imported. */
export function changeText(previous: string | null, spent: string): string {
  if (previous === null) return "—";
  const cents = Math.round(Number(spent) * 100) - Math.round(Number(previous) * 100);
  if (cents === 0) return "same";
  return `${cents > 0 ? "+" : "−"}${formatMoney((Math.abs(cents) / 100).toFixed(2))}`;
}

/** "Up $20.67 (33%)", "Down …", "No change", "New this month", or null when last month wasn't imported. */
function changeLong(previous: string | null, spent: string): string | null {
  if (previous === null) return null;
  const cents = Math.round(Number(spent) * 100) - Math.round(Number(previous) * 100);
  if (cents === 0) return "No change";
  if (Number(previous) === 0) return "New this month";
  const pct = Math.round((Math.abs(cents) / Math.round(Number(previous) * 100)) * 100);
  return `${cents > 0 ? "Up" : "Down"} ${formatMoney((Math.abs(cents) / 100).toFixed(2))} (${pct}%)`;
}

const changeClass = (previous: string | null, spent: string) => {
  const t = changeText(previous, spent);
  return t.startsWith("+") ? "is-up" : t.startsWith("−") ? "is-down" : "";
};

const STATUS_COLOR: Record<BudgetStatus, string> = {
  ok: "var(--jewel-emerald)", near: "var(--jewel-topaz)", over: "var(--error)", none: "var(--muted)",
};

/** A ring filled to percent (0–100 shown; more is a full ring). */
function Ring({ percent, size, stroke, color, track, label, children }: {
  percent: number | null; size: number; stroke: number; color: string; track: string; label?: string;
  children?: ReactNode;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const filled = percent === null ? 0 : (Math.max(0, Math.min(100, percent)) / 100) * c;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role={label ? "img" : undefined}
      aria-label={label} aria-hidden={label ? undefined : true} className="ring">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={track} strokeWidth={stroke} />
      {filled > 0 && (
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={`${filled.toFixed(2)} ${c.toFixed(2)}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      )}
      {children}
    </svg>
  );
}

export function BudgetsPage() {
  const [month, setMonth] = useState<string | null>(null); // null until the API picks the default month
  const [page, setPage] = useState<Page | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [open, setOpen] = useState<string | null>(null); // the category whose detail row is open
  const [copying, setCopying] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const request = useRef(0);

  const load = useCallback(async (target: string | null, quiet = false) => {
    const id = ++request.current;
    if (!quiet) setStatus("loading");
    try {
      const next = await getBudgets(target ?? undefined);
      if (id !== request.current) return;
      setPage(next);
      setMonth(next.month);
      setStatus("ready");
    } catch (err) {
      if (id === request.current && !isUnauthorized(err) && !quiet) setStatus("error");
    }
  }, []);

  useEffect(() => {
    void load(null);
  }, [load]);

  function go(n: number) {
    if (!month) return;
    setOpen(null);
    setActionError(null);
    void load(shiftMonth(month, n));
  }

  async function fillBlanks() {
    if (!month) return;
    setCopying(true);
    setActionError(null);
    try {
      await copyBudgets(shiftMonth(month, -1), month);
      await load(month, true);
    } catch (err) {
      if (!isUnauthorized(err)) setActionError(`Couldn't copy ${monthName(shiftMonth(month, -1))}'s budgets. Try again.`);
    } finally {
      setCopying(false);
    }
  }

  const reload = useCallback(() => (month ? load(month, true) : Promise.resolve()), [load, month]);

  return (
    <main className="page budgets-page">
      <header className="budgets-head">
        <h1>Budgets</h1>
        {month && (
          <div className="budgets-tools">
            <nav className="month-switch" aria-label="Month">
              <button type="button" aria-label="Previous month" onClick={() => go(-1)}>‹</button>
              <span aria-live="polite">{monthLabel(month)}</span>
              <button type="button" aria-label="Next month" onClick={() => go(1)}
                disabled={!page || month >= page.max_month}>›</button>
            </nav>
            {page && status === "ready" && page.previous_month_count > 0 && page.budget_count < page.rows.length && (
              <button type="button" className="secondary" onClick={() => void fillBlanks()} disabled={copying}
                title={`Copy ${monthName(shiftMonth(month, -1))}'s limits into categories with no budget this month`}>
                {copying ? "Filling…" : `Fill blanks from ${monthName(shiftMonth(month, -1))}`}
              </button>
            )}
          </div>
        )}
      </header>

      {status === "error" ? (
        <div className="card notice" role="alert">
          <p>Couldn't load your budgets.</p>
          <button type="button" onClick={() => void load(month)}>Retry</button>
        </div>
      ) : status === "loading" || page === null ? (
        <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading budgets…</p>
      ) : (
        <>
          <Banner page={page} />
          {actionError && <p className="error" role="alert">{actionError}</p>}

          <div className="card budget-table-wrap">
            <table className="budget-table">
              <thead>
                <tr>
                  <th scope="col">Category</th>
                  <th scope="col" className="num">Budget</th>
                  <th scope="col" className="num">Spent</th>
                  <th scope="col" className="num col-left">Left</th>
                  <th scope="col">Used</th>
                  <th scope="col" className="num col-prev">
                    vs {monthName(shiftMonth(page.month, -1)).slice(0, 3)}
                  </th>
                </tr>
              </thead>
              <tbody>
                {page.rows.map((r) => {
                  const isOpen = open === r.category;
                  const detailId = `budget-detail-${r.category}`;
                  return (
                    <Fragment key={r.category}>
                      <tr className={isOpen ? "is-open" : undefined}>
                        <th scope="row">
                          <button type="button" className="budget-name" aria-expanded={isOpen} aria-controls={detailId}
                            onClick={() => setOpen(isOpen ? null : r.category)}>
                            <span className="chevron" aria-hidden="true">›</span>
                            <span className="swatch" aria-hidden="true" style={{ "--chip": jewel(r.category) } as CSSProperties} />
                            {r.category}
                          </button>
                        </th>
                        <td className="num">
                          <BudgetCell month={page.month} row={r} onChanged={reload} />
                        </td>
                        <td className="num">{money(r.spent)}</td>
                        <td className={`num col-left${r.status === "over" ? " is-over" : ""}`}>
                          {r.left === null ? "—" : r.left.startsWith("-") ? `${money(r.left)} over` : money(r.left)}
                        </td>
                        <td>
                          <span className="used">
                            <Ring percent={r.percent} size={30} stroke={4} color={STATUS_COLOR[r.status]} track="var(--surface-2)" />
                            <span style={{ color: STATUS_COLOR[r.status] }}>{r.percent === null ? "—" : `${r.percent}%`}</span>
                          </span>
                        </td>
                        <td className={`num col-prev ${changeClass(r.previous_spent, r.spent)}`}>
                          {changeText(r.previous_spent, r.spent)}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="budget-detail-row" id={detailId}>
                          <td colSpan={6}>
                            <Detail month={page.month} row={r} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Total budgeted</th>
                  <td className="num">{money(page.budgeted)}</td>
                  <td className="num">{money(page.spent)}</td>
                  <td className={`num col-left${page.left.startsWith("-") ? " is-over" : ""}`}>
                    {page.left.startsWith("-") ? `${money(page.left)} over` : money(page.left)}
                  </td>
                  <td>{page.budget_count > 0 ? `${totalPercent(page)}%` : "—"}</td>
                  <td className="col-prev" />
                </tr>
              </tfoot>
            </table>
          </div>

          <p className="caption">
            Click a category to see its purchases. Type a budget and press Enter to save; Esc undoes your typing;
            clear it to remove the budget. Income and Transfers aren't budgeted.
          </p>
        </>
      )}
    </main>
  );
}

const totalPercent = (page: Page) => Math.round((Number(page.spent) / Number(page.budgeted)) * 100);

function Banner({ page }: { page: Page }) {
  const has = page.budget_count > 0;
  const pct = has ? totalPercent(page) : null;
  const over = page.left.startsWith("-");
  const overNames = page.rows.filter((r) => r.status === "over").map((r) => r.category);
  return (
    <section className="budget-banner" aria-label="Month total">
      <Ring percent={pct} size={112} stroke={11} color={over ? "var(--banner-over)" : "var(--brass-active)"}
        track="color-mix(in srgb, var(--on-ink) 14%, transparent)"
        label={has ? `${pct}% of the month's budget spent` : "No budgets this month"}>
        <text x="56" y="63" textAnchor="middle" className="ring-label">{has ? `${pct}%` : "—"}</text>
      </Ring>
      {has ? (
        <div className="banner-stats">
          <div>
            <span className="banner-label">Spent</span>
            <span className="banner-value">{money(page.spent)}</span>
            <span className="banner-sub">of {money(page.budgeted)} budgeted</span>
          </div>
          <div>
            <span className="banner-label">{over ? "Over by" : "Left"}</span>
            <span className="banner-value">{money(page.left)}</span>
            <span className="banner-sub">across {plural(page.budget_count, "budget", "budgets")}</span>
          </div>
          <div>
            <span className="banner-label">Over</span>
            <span className="banner-value">{page.over_count}</span>
            <span className="banner-sub">{overNames.length > 0 ? overNames.join(", ") : "all within budget"}</span>
          </div>
        </div>
      ) : (
        <div className="banner-empty">
          <span className="banner-value">No budgets for {monthLabel(page.month)}</span>
          <span className="banner-sub">Type an amount in the Budget column to set one.</span>
        </div>
      )}
      <p className="banner-foot">
        {page.as_of ? <>Through {formatDate(page.as_of)}.</> : <><Link to="/transactions">Import a statement</Link> to start budgeting.</>}
        {page.pending_review_count > 0 && (
          <>
            {" "}{plural(page.pending_review_count, "transaction", "transactions")}{" "}
            {page.pending_review_count === 1 ? "is" : "are"} waiting in review and{" "}
            {page.pending_review_count === 1 ? "isn't" : "aren't"} counted yet. <Link to="/review">Review them</Link>
          </>
        )}
      </p>
    </section>
  );
}

type Flash = { kind: "saved" } | { kind: "removed"; limit: string } | { kind: "error"; text: string };

/** The Budget cell: Enter or leaving the cell saves, Esc puts the old value back, clearing it removes the budget. */
function BudgetCell({ month, row, onChanged }: { month: string; row: BudgetRow; onChanged: () => Promise<void> }) {
  const original = row.limit ?? "";
  const [value, setValue] = useState(original);
  const [flash, setFlash] = useState<Flash | null>(null);
  const focused = useRef(false);
  const busy = useRef(false);
  const escaped = useRef(false); // Esc blurs the input; that blur must not save what was typed
  const input = useRef<HTMLInputElement>(null);

  // Take the saved value from the server unless the user is typing in this cell.
  useEffect(() => {
    if (!focused.current) setValue(original);
  }, [original]);

  useEffect(() => {
    if (!flash || flash.kind === "error") return;
    const t = setTimeout(() => setFlash(null), flash.kind === "saved" ? SAVED_MS : UNDO_MS);
    return () => clearTimeout(t);
  }, [flash]);

  const unchanged = (v: string) => (v === "" ? original === "" : original !== "" && Number(cleanLimit(v)) === Number(original));

  async function commit(source: "enter" | "blur") {
    if (busy.current) return;
    const v = value.trim();
    if (unchanged(v)) {
      if (flash?.kind === "error") setFlash(null);
      return;
    }
    if (v === "") {
      if (row.budget_id === null) return;
      busy.current = true;
      try {
        await deleteBudget(row.budget_id);
        setFlash({ kind: "removed", limit: original });
        await onChanged();
      } catch (err) {
        if (!isUnauthorized(err)) {
          setValue(original);
          setFlash({ kind: "error", text: "Couldn't remove this budget. Try again." });
        }
      } finally {
        busy.current = false;
      }
      return;
    }
    const problem = limitError(v);
    if (problem) {
      if (source === "blur") {
        setValue(original);
        setFlash({ kind: "error", text: `Not saved. ${problem}` });
      } else {
        setFlash({ kind: "error", text: problem });
      }
      return;
    }
    busy.current = true;
    try {
      const saved = await setBudget(month, row.category, cleanLimit(v));
      if (!focused.current) setValue(saved.limit ?? "");
      setFlash({ kind: "saved" });
      await onChanged();
    } catch (err) {
      if (!isUnauthorized(err)) {
        setFlash({ kind: "error", text: err instanceof ApiError && err.status === 422
          ? "Enter an amount above $0, like 150 or 150.00." : "Couldn't save. Try again." });
      }
    } finally {
      busy.current = false;
    }
  }

  async function undo() {
    if (flash?.kind !== "removed") return;
    const limit = flash.limit;
    setFlash(null);
    try {
      await setBudget(month, row.category, limit);
      setValue(limit);
      await onChanged();
    } catch (err) {
      if (!isUnauthorized(err)) setFlash({ kind: "error", text: "Couldn't put it back. Type the amount again." });
    }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      void commit("enter");
    } else if (e.key === "Escape") {
      escaped.current = true;
      setValue(original);
      setFlash(null);
      input.current?.blur();
    }
  }

  const errorId = `budget-error-${row.category}`;
  return (
    <div className="budget-cell">
      <label className={`budget-input${original === "" ? " is-empty" : ""}${flash?.kind === "error" ? " is-invalid" : ""}`}>
        <span aria-hidden="true">$</span>
        <input ref={input} inputMode="decimal" value={value} placeholder="Set" aria-label={`${row.category} budget`}
          aria-invalid={flash?.kind === "error"} aria-describedby={flash?.kind === "error" ? errorId : undefined}
          onChange={(e) => setValue(e.target.value)} onKeyDown={onKey}
          onFocus={() => { focused.current = true; }}
          onBlur={() => {
            focused.current = false;
            if (escaped.current) escaped.current = false;
            else void commit("blur");
          }} />
      </label>
      {flash?.kind === "saved" && <span className="cell-flash" role="status">Saved</span>}
      {flash?.kind === "removed" && (
        <span className="cell-flash" role="status">
          Removed · <button type="button" className="link" onClick={() => void undo()}>Undo</button>
        </span>
      )}
      {flash?.kind === "error" && <span id={errorId} className="cell-flash error" role="alert">{flash.text}</span>}
    </div>
  );
}

/** The open row: last month vs this month, the largest purchase, and the biggest purchases (counted ones only). */
function Detail({ month, row }: { month: string; row: BudgetRow }) {
  const [items, setItems] = useState<TransactionOut[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);

  useEffect(() => {
    let live = true;
    // counted=true: the same rows the Spent figure adds up, never ones still waiting in review
    listTransactions({ month, category: row.category, counted: true }, { limit: 200, offset: 0 })
      .then((p) => {
        if (!live) return;
        setItems([...p.items].sort((a, b) => Number(a.amount) - Number(b.amount)));
        setTotal(p.total);
      })
      .catch((err) => { if (live && !isUnauthorized(err)) setError(true); });
    return () => { live = false; };
  }, [month, row.category, row.spent]);

  const previous = monthName(shiftMonth(month, -1));
  const change = changeLong(row.previous_spent, row.spent);
  const largest = items?.find((t) => Number(t.amount) < 0);
  const shown = items?.slice(0, DETAIL_PURCHASES) ?? [];
  const query = new URLSearchParams({ month, category: row.category }).toString();

  return (
    <div className="budget-detail">
      <div className="detail-facts">
        <div className="phone-only">
          <span className="caption">Left</span>
          <span className={row.status === "over" ? "is-over" : undefined}>
            {row.left === null ? "No budget" : row.left.startsWith("-") ? `${money(row.left)} over` : money(row.left)}
          </span>
        </div>
        <div>
          <span className="caption">{previous} → {monthName(month)}</span>
          {row.previous_spent === null ? (
            <span>{previous} wasn't imported</span>
          ) : (
            <>
              <span>{money(row.previous_spent)} → {money(row.spent)}</span>
              <span className={changeClass(row.previous_spent, row.spent)}>{change}</span>
            </>
          )}
        </div>
        <div>
          <span className="caption">Largest purchase</span>
          <span>{largest ? `${largest.merchant ?? largest.description} · ${money(largest.amount)}` : "—"}</span>
        </div>
      </div>
      <div className="detail-purchases">
        {error ? (
          <p className="error" role="alert">Couldn't load these purchases.</p>
        ) : items === null ? (
          <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading purchases…</p>
        ) : total === 0 ? (
          <p className="caption">No purchases in {row.category} this month.</p>
        ) : (
          <>
            <h3 className="detail-heading">
              {total > DETAIL_PURCHASES ? `${DETAIL_PURCHASES} largest of ${total} purchases` : plural(total, "purchase", "purchases")}
            </h3>
            <ul className="detail-list" aria-label={`${row.category} purchases`}>
              {shown.map((t) => (
                <li key={t.id}>
                  <span className="caption">{formatDate(t.date).replace(/, \d{4}$/, "")}</span>
                  <span className="detail-merchant">{t.merchant ?? t.description}</span>
                  <span className="num">{Number(t.amount) > 0 ? `+${money(t.amount)}` : money(t.amount)}</span>
                </li>
              ))}
            </ul>
            <Link to={`/transactions?${query}`}>
              {total > DETAIL_PURCHASES ? `See all ${total} in Transactions` : "Open in Transactions"}
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
