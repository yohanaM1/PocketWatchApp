import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { createAccount, importCsv, listAccounts } from "../api/accounts";
import { ApiError } from "../api/client";
import { listCategories, listTransactions, setCategory, type TransactionFilters } from "../api/transactions";
import type { AccountOut, AccountType, ImportErrorDetail, ImportResult, TransactionOut, TransactionPage } from "../api/types";
import { jewel } from "../lib/categories";
import { notifyReviewChanged } from "../lib/reviewEvents";

const PAGE = 50;
const MAX_PAGE = 200; // the backend's limit cap
const UNCATEGORIZED = "uncategorized";
const MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct 5, 2026" from "2026-10-05", without going through Date (no timezone shift). */
export function formatDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${MONTH_ABBR[m - 1]} ${d}, ${y}`;
}

/** "−$1,234.56" for money out, "+$200.00" for money in. String-only, so no float rounding. */
export function formatAmount(amount: string): string {
  const out = amount.startsWith("-");
  const [whole, cents] = (out ? amount.slice(1) : amount).split(".");
  return `${out ? "−" : "+"}$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${cents}`;
}

/** "$1,234.56" from a positive 2-decimal string. */
export function formatMoney(amount: string): string {
  const [whole, cents] = amount.split(".");
  return `$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${cents}`;
}

/** "in September 2026" for a YYYY-MM filter, else "across all months". */
function scope(month: string): string {
  if (!month) return "across all months";
  const [y, m] = month.split("-").map(Number);
  return `in ${["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"][m - 1]} ${y}`;
}

type Totals = Pick<TransactionPage, "money_out" | "money_in" | "uncategorized_count" | "review_count">;

function StatTiles({ totals, month, onReview }: { totals: Totals; month: string; onReview: () => void }) {
  const n = totals.review_count; // uncategorized plus unconfirmed guesses, the same count as the Review page
  return (
    <section className="tiles" aria-label="Totals">
      <div className="tile" style={{ "--tile": "var(--jewel-ruby)" } as CSSProperties}>
        <span className="tile-label">Money out</span>
        <span className="tile-value">{formatMoney(totals.money_out)}</span>
        <span className="caption">{scope(month)}</span>
      </div>
      <div className="tile" style={{ "--tile": "var(--jewel-emerald)" } as CSSProperties}>
        <span className="tile-label">Money in</span>
        <span className="tile-value">{formatMoney(totals.money_in)}</span>
        <span className="caption">{scope(month)}</span>
      </div>
      <div className="tile" style={{ "--tile": "var(--jewel-topaz)" } as CSSProperties}>
        <span className="tile-label">Need a category</span>
        <span className="tile-value">{n}</span>
        {n > 0
          ? <button type="button" className="link" onClick={onReview}>Review {n === 1 ? "it" : "them"}</button>
          : <span className="caption">All sorted</span>}
      </div>
    </section>
  );
}

const isUnauthorized = (err: unknown) => err instanceof ApiError && err.status === 401; // the client already logs out

type ImportProblem = { message: string; lines: ImportErrorDetail["errors"] };

function importProblem(err: unknown): ImportProblem {
  if (err instanceof ApiError) {
    const d = err.detail as Partial<ImportErrorDetail> | string | null;
    if (typeof d === "string") return { message: d, lines: [] };
    if (d && typeof d.message === "string") return { message: d.message, lines: d.errors ?? [] };
  }
  return { message: "Couldn't upload the file. Please try again.", lines: [] };
}

function AddAccountForm({ onAdded, onCancel }: { onAdded: (a: AccountOut) => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [type, setType] = useState<AccountType>("checking");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onAdded(await createAccount(name, type));
    } catch (err) {
      if (!isUnauthorized(err)) setError("Couldn't add the account. Check the name and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="inline-form" onSubmit={submit} aria-label="Add account">
      <label>
        Account name
        <input value={name} required maxLength={100} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Type
        <select value={type} onChange={(e) => setType(e.target.value as AccountType)}>
          <option value="checking">Checking</option>
          <option value="savings">Savings</option>
          <option value="credit">Credit</option>
        </select>
      </label>
      <button type="submit" disabled={busy || !name.trim()}>Add</button>
      <button type="button" className="link" onClick={onCancel}>Cancel</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

function ImportCard({ accounts, onAccountAdded, onImported }: {
  accounts: AccountOut[];
  onAccountAdded: (a: AccountOut) => void;
  onImported: () => void;
}) {
  const [chosen, setChosen] = useState<number | null>(null);
  const accountId = chosen ?? accounts[0]?.id ?? null; // registration creates "Checking", so this is rarely null
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [problem, setProblem] = useState<ImportProblem | null>(null);
  const [adding, setAdding] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function upload(e: FormEvent) {
    e.preventDefault();
    if (accountId === null || !file) return;
    setBusy(true);
    setResult(null);
    setProblem(null);
    try {
      setResult(await importCsv(accountId, file));
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      onImported();
    } catch (err) {
      if (!isUnauthorized(err)) setProblem(importProblem(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="import-title">
      <h2 id="import-title">Import transactions</h2>
      <form className="inline-form" onSubmit={upload}>
        <label>
          Account
          <select value={accountId ?? ""} onChange={(e) => setChosen(Number(e.target.value))}>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label>
          CSV file
          <input ref={fileInput} type="file" accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <button type="submit" disabled={busy || accountId === null || !file}>{busy ? "Uploading…" : "Upload"}</button>
      </form>
      <p className="caption">
        Columns: date, description, amount (negative = money out). Rows you already imported are skipped.{" "}
        {!adding && <button type="button" className="link" onClick={() => setAdding(true)}>Add another account</button>}
      </p>
      {adding && (
        <AddAccountForm
          onCancel={() => setAdding(false)}
          onAdded={(a) => {
            onAccountAdded(a);
            setChosen(a.id);
            setAdding(false);
          }}
        />
      )}
      {result && (
        <p className="success" role="status">
          Imported {result.imported} of {result.total_rows} rows
          {result.skipped_duplicates > 0 && ` · skipped ${result.skipped_duplicates} already imported`}
          {(result.needs_review ?? 0) > 0 && ` · ${result.needs_review} waiting in review`}.
        </p>
      )}
      {problem && (
        <div role="alert">
          <p className="error">{problem.message}</p>
          {problem.lines.length > 0 && (
            <ul className="error-list">
              {problem.lines.map((l) => <li key={`${l.line}:${l.message}`}>Line {l.line}: {l.message}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

interface Prompt {
  id: number;
  merchant: string;
  category: string;
  count: number;
}

export function TransactionsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const month = params.get("month") ?? "";
  const account = params.get("account") ?? "";
  const category = params.get("category") ?? "";
  const filtered = Boolean(month || account || category);
  const filters = useMemo<TransactionFilters>(
    () => ({ month: month || undefined, accountId: account ? Number(account) : undefined, category: category || undefined }),
    [month, account, category],
  );

  const [accounts, setAccounts] = useState<AccountOut[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [items, setItems] = useState<TransactionOut[]>([]);
  const [total, setTotal] = useState(0);
  const [totals, setTotals] = useState<Totals | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [saving, setSaving] = useState(false); // one category save at a time
  const [rowError, setRowError] = useState<{ id: number; message: string } | null>(null);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [changed, setChanged] = useState<{ id: number; from: string } | null>(null); // the last single change, for Undo
  const request = useRef(0); // ignores responses for filters that are no longer current

  useEffect(() => {
    listAccounts().then(setAccounts).catch(() => {});
    listCategories().then(setCategories).catch(() => {});
  }, []);

  const load = useCallback(async (limit: number) => {
    const id = ++request.current;
    setStatus("loading");
    try {
      const page = await listTransactions(filters, { limit, offset: 0 });
      if (id !== request.current) return;
      setItems(page.items);
      setTotal(page.total);
      setTotals({ money_out: page.money_out, money_in: page.money_in, uncategorized_count: page.uncategorized_count,
        review_count: page.review_count });
      setStatus("ready");
    } catch (err) {
      if (id === request.current && !isUnauthorized(err)) setStatus("error");
    }
  }, [filters]);

  useEffect(() => {
    setItems([]);
    setPrompt(null);
    setNotice(null);
    setRowError(null);
    void load(PAGE);
  }, [load]);

  const reload = () => load(Math.min(Math.max(items.length, PAGE), MAX_PAGE));

  async function loadMore() {
    const id = request.current;
    setLoadingMore(true);
    try {
      const page = await listTransactions(filters, { limit: PAGE, offset: items.length });
      if (id === request.current) {
        setItems((prev) => [...prev, ...page.items]);
        setTotal(page.total);
      }
    } catch {
      // keep what we have; the button stays
    } finally {
      setLoadingMore(false);
    }
  }

  function setFilter(key: string, value: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });
  }

  async function changeCategory(t: TransactionOut, next: string) {
    setSaving(true);
    setRowError(null);
    setPrompt(null);
    setNotice(null);
    setChanged(null);
    try {
      const res = await setCategory(t.id, next);
      notifyReviewChanged();
      setItems((prev) => prev.map((x) => (x.id === t.id ? res.transaction : x)));
      // So a wrong pick isn't forgotten. Not for a guess: "undo" would confirm the guess, not send it back to review.
      if (t.category !== null && !t.in_review) setChanged({ id: t.id, from: t.category });
      if (t.in_review) {
        setTotals((prev) => prev && {
          ...prev, review_count: Math.max(prev.review_count - 1, 0),
          uncategorized_count: t.category === null ? Math.max(prev.uncategorized_count - 1, 0) : prev.uncategorized_count,
        });
      }
      if (res.same_merchant_count > 0 && t.merchant) {
        setPrompt({ id: t.id, merchant: t.merchant, category: next, count: res.same_merchant_count });
      }
    } catch (err) {
      // the select is controlled by the row, so it already shows the old category again
      if (!isUnauthorized(err)) setRowError({ id: t.id, message: "Couldn't save the category. Try again." });
    } finally {
      setSaving(false);
    }
  }

  /** Put the last changed transaction back in the category it had (as a category you set). */
  async function undoChange(t: TransactionOut, from: string) {
    setSaving(true);
    setRowError(null);
    setPrompt(null);
    try {
      const res = await setCategory(t.id, from);
      notifyReviewChanged();
      setItems((prev) => prev.map((x) => (x.id === t.id ? res.transaction : x)));
      setChanged(null);
    } catch (err) {
      if (!isUnauthorized(err)) setRowError({ id: t.id, message: `Couldn't change it back to ${from}. Try again.` });
    } finally {
      setSaving(false);
    }
  }

  async function applyToMerchant(p: Prompt) {
    setSaving(true);
    try {
      const res = await setCategory(p.id, p.category, true);
      notifyReviewChanged();
      setPrompt(null);
      setChanged(null); // Undo would only put back this one row, not the others
      const noun = res.also_updated === 1 ? "transaction" : "transactions";
      setNotice(`Also set ${res.also_updated} other ${p.merchant} ${noun} to ${p.category}. Future imports will use it too.`);
      await reload();
    } catch (err) {
      if (!isUnauthorized(err)) setRowError({ id: p.id, message: "Couldn't apply it to the other transactions. Try again." });
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="page wide">
      <header className="page-header">
        <h1>Transactions</h1>
        <p className="caption">Import statements, check what came in and went out, and set categories.</p>
      </header>

      {totals && (
        <StatTiles totals={totals} month={month} onReview={() => navigate("/review")} />
      )}

      <ImportCard
        accounts={accounts}
        onAccountAdded={(a) => setAccounts((prev) => [...prev, a])}
        onImported={() => { notifyReviewChanged(); void reload(); }}
      />

      <section className="card stack" aria-labelledby="txn-title">
        <h2 id="txn-title">All transactions</h2>
        <div className="filters">
          <label>
            Month
            <input type="month" value={month} onChange={(e) => setFilter("month", e.target.value)} />
          </label>
          <label>
            Account
            <select value={account} onChange={(e) => setFilter("account", e.target.value)}>
              <option value="">All accounts</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
          <label>
            Category
            <select value={category} onChange={(e) => setFilter("category", e.target.value)}>
              <option value="">All categories</option>
              <option value={UNCATEGORIZED}>Uncategorized</option>
              {categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          {filtered && <button type="button" className="link" onClick={() => setParams({}, { replace: true })}>Clear filters</button>}
        </div>

        {notice && <p className="success" role="status">{notice}</p>}

        {status === "error" ? (
          <div className="notice" role="alert">
            <p>Couldn't load your transactions.</p>
            <button type="button" onClick={() => void reload()}>Retry</button>
          </div>
        ) : status === "loading" && items.length === 0 ? (
          <p className="waiting" role="status"><span className="spinner" aria-hidden="true" /> Loading transactions…</p>
        ) : items.length === 0 ? (
          filtered ? (
            <p className="caption">No transactions match these filters.</p>
          ) : (
            <p className="caption">No transactions yet. Import a CSV above to get started.</p>
          )
        ) : (
          <>
            <p className="caption">Showing {items.length} of {total}</p>
            <div className="table-wrap">
              <table className="txn-table">
                <thead>
                  <tr><th>Date</th><th>Merchant</th><th>Account</th><th>Category</th><th className="amount">Amount</th></tr>
                </thead>
                <tbody>
                  {items.map((t) => (
                    <TransactionRow
                      key={t.id}
                      t={t}
                      categories={categories}
                      disabled={saving}
                      error={rowError?.id === t.id ? rowError.message : null}
                      prompt={prompt?.id === t.id ? prompt : null}
                      was={changed?.id === t.id ? changed.from : null}
                      onUndo={(from) => void undoChange(t, from)}
                      onChange={(c) => void changeCategory(t, c)}
                      onApply={(p) => void applyToMerchant(p)}
                      onDismiss={() => setPrompt(null)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            {items.length < total && (
              <button type="button" className="link" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading…" : "Show more"}
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}

function TransactionRow({ t, categories, disabled, error, prompt, was, onUndo, onChange, onApply, onDismiss }: {
  t: TransactionOut;
  categories: string[];
  disabled: boolean;
  error: string | null;
  prompt: Prompt | null;
  was: string | null; // the category before the last change, while Undo is offered
  onUndo: (from: string) => void;
  onChange: (category: string) => void;
  onApply: (p: Prompt) => void;
  onDismiss: () => void;
}) {
  const name = t.merchant ?? t.description;
  const options = categories.length ? categories : t.category ? [t.category] : [];
  return (
    <>
      <tr>
        <td className="c-date nowrap">{formatDate(t.date)}</td>
        <td className="c-merchant">
          <div className="merchant-name">{name}</div>
          {t.merchant && <div className="caption">{t.description}</div>}
        </td>
        <td className="c-account">{t.account_name}</td>
        <td className="c-category">
          <select className={!t.category ? "cat-select empty" : t.in_review ? "cat-select guess" : "cat-select"}
            style={{ "--chip": jewel(t.category) } as CSSProperties}
            aria-label={`Category for ${name} on ${formatDate(t.date)}`} value={t.category ?? ""}
            disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            {t.category === null && <option value="" disabled>Uncategorized</option>}
            {options.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          {t.in_review && t.category && (
            <p className="caption guess-note">
              Guess{t.category_confidence !== null && ` · ${Math.round(t.category_confidence * 100)}%`} · waiting in review,
              not counted yet ·{" "}
              {/* picking the category a select already shows fires no change, so a right guess needs its own button */}
              <button type="button" className="link" disabled={disabled} onClick={() => onChange(t.category as string)}
                aria-label={`Confirm ${name} as ${t.category}`}>Confirm</button>
            </p>
          )}
          {was && (
            <p className="caption was-note" role="status">
              Was {was} · <button type="button" className="link" disabled={disabled} onClick={() => onUndo(was)}
                aria-label={`Undo: change ${name} back to ${was}`}>Undo</button>
            </p>
          )}
          {error && <p className="error" role="alert">{error}</p>}
        </td>
        <td className={t.amount.startsWith("-") ? "c-amount amount" : "c-amount amount money-in"}>{formatAmount(t.amount)}</td>
      </tr>
      {prompt && (
        <tr className="prompt-row">
          <td colSpan={5}>
            <span>
              Also set {prompt.count} other {prompt.merchant} {prompt.count === 1 ? "transaction" : "transactions"} to{" "}
              {prompt.category}, and use it for future imports?
            </span>
            <span className="row-actions">
              <button type="button" disabled={disabled} onClick={() => onApply(prompt)}>Apply</button>
              <button type="button" className="link" onClick={onDismiss}>Just this one</button>
            </span>
          </td>
        </tr>
      )}
    </>
  );
}
