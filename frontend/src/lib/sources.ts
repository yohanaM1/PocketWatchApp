import { isCountResult, isListPlan } from "../api/types";
import type { AmountRow, CountResult, FlagResult, Plan, RecurringResult, Slot, SpendingResult, ToolResult } from "../api/types";

// The source rows behind an answer, straight from the stored SQL results. Nothing here computes a figure:
// amounts are the tool's own `spending` strings, only formatted for display.

export interface SourceRow {
  rowId: string;
  what: string;
  period: string;
  spending: string | null; // null for a purchase count, which has no amount
  refundsExceeded: boolean;
  count: number;
}

export interface SourceTransaction {
  rowId: string;
  date: string;
  merchant: string;
  category: string;
  amount: string;
}

export interface SourceSeries {
  rowId: string;
  merchant: string;
  amount: string; // "$8.99"
  next: string; // "Nov 18, 2026"
}

export interface SourceFlag {
  rowId: string;
  label: string;
  date: string;
  merchant: string;
  amount: string;
}

export interface Sources {
  noData: boolean;
  rows: SourceRow[];
  countsPurchases: boolean; // rows count money-out purchases (transaction_count), not all transactions
  transactions: SourceTransaction[]; // the listed rows behind a count, at most the tool's row cap
  truncated: boolean;
  series: SourceSeries[]; // recurring_list: the active series behind the answer
  stopped: number; // recurring_list: how many series have stopped
  flags: SourceFlag[]; // flag_list: the open flags behind the answer
  dataThrough: string | null; // list answers: "Oct 30, 2026", the latest imported transaction
}

export const FLAG_LABELS: Record<FlagResult["rows"][number]["kind"], string> = {
  duplicate: "Possible duplicate", large_charge: "Unusually large", unfamiliar_merchant: "New merchant",
};

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];
const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

function parts(iso: string): [number, number, number] {
  const [y, m, d] = iso.split("-").map(Number);
  return [y, m, d];
}

/** "Sep 1 – Sep 30, 2026", "Dec 15, 2025 – Jan 14, 2026", "Oct 1, 2026". */
export function formatPeriod(start: string, end: string): string {
  const [y1, m1, d1] = parts(start);
  const [y2, m2, d2] = parts(end);
  if (start === end) return `${MON[m2 - 1]} ${d2}, ${y2}`;
  const left = y1 === y2 ? `${MON[m1 - 1]} ${d1}` : `${MON[m1 - 1]} ${d1}, ${y1}`;
  return `${left} – ${MON[m2 - 1]} ${d2}, ${y2}`;
}

/** "September 2026" for a whole calendar month, else the formatted range. */
export function periodName(start: string, end: string): string {
  const [y1, m1, d1] = parts(start);
  const [y2, m2, d2] = parts(end);
  const lastDay = new Date(Date.UTC(y1, m1, 0)).getUTCDate();
  if (y1 === y2 && m1 === m2 && d1 === 1 && d2 === lastDay) return `${FULL[m1 - 1]} ${y1}`;
  return formatPeriod(start, end);
}

export function formatSpending(spending: string): string {
  return usd.format(Number(spending));
}

function row(r: AmountRow, what: string, result: SpendingResult): SourceRow {
  return {
    rowId: r.row_id,
    what,
    period: formatPeriod(result.period.start, result.period.end),
    spending: formatSpending(r.spending),
    refundsExceeded: Number(r.spending) < 0,
    count: r.count,
  };
}

const EMPTY = { countsPurchases: false, transactions: [], truncated: false, series: [], stopped: 0, flags: [],
  dataThrough: null };

/** "$8.99" from a money-out amount string ("-8.99"). */
const charge = (amount: string) => usd.format(Math.abs(Number(amount)));
const day = (iso: string) => formatPeriod(iso, iso);

/** The active series or open flags behind a list answer, straight from the stored result. */
function listSources(intent: "recurring_list" | "flag_list", result: RecurringResult | FlagResult): Sources {
  const through = result.coverage.last_date;
  const base = { ...EMPTY, noData: result.coverage.count === 0, rows: [], dataThrough: through ? day(through) : null };
  if (intent === "recurring_list") {
    const r = result as RecurringResult;
    return { ...base, stopped: r.stopped.count,
      series: r.rows.map((s) => ({ rowId: s.row_id, merchant: s.merchant, amount: charge(s.amount), next: day(s.next_expected) })) };
  }
  const f = result as FlagResult;
  return { ...base, flags: f.rows.map((x) => ({ rowId: x.row_id, label: FLAG_LABELS[x.kind], date: day(x.date),
    merchant: x.merchant, amount: charge(x.amount) })) };
}

/** What a marked value in a list answer refers to. */
function describeListSlot(slot: Slot, src: Sources): string {
  if (slot.row_id === "coverage") return "Your latest imported transaction";
  if (slot.row_id === "rectotal") return "Your active recurring payments, added up";
  if (slot.row_id === "stopped") return "Recurring payments that have stopped";
  if (slot.row_id === "flagcount") return "Your open flags";
  const series = src.series.find((s) => s.rowId === slot.row_id);
  if (series) return slot.field === "next_expected" ? `${series.merchant} · next expected` : `${series.merchant} · a month`;
  const flag = src.flags.find((f) => f.rowId === slot.row_id);
  return flag ? `${flag.label} · ${flag.merchant}` : "Verified";
}

/** "Dining", "Dining + Groceries" or "All spending categories". */
function categoriesLabel(categories: string[] | "all_spending"): string {
  return categories === "all_spending" ? "All spending categories" : categories.join(" + ");
}

/** One row with the purchase count, then the transactions it was counted from. */
function countSources(categories: string[] | "all_spending", result: CountResult): Sources {
  const c = result.count_row;
  return {
    ...EMPTY,
    noData: false,
    rows: [{ rowId: c.row_id, what: categoriesLabel(categories), period: formatPeriod(result.period.start, result.period.end),
      spending: null, refundsExceeded: false, count: c.money_out_count }],
    countsPurchases: true,
    transactions: result.rows.map((t) => ({ rowId: t.row_id, date: formatPeriod(t.date, t.date),
      merchant: t.merchant ?? "Unknown merchant", category: t.category ?? "Not categorized", amount: usd.format(Number(t.amount)) })),
    truncated: result.truncated,
  };
}

/** Planned categories in plan order (or the total), then the selection, then uncategorized if any.
 * A count answer gets its count row and the transactions behind it instead. */
export function sourceRows(plan: Plan | null, results: ToolResult[] | null): Sources | null {
  if (!plan || plan.intent === "no_data" || !results || results.length === 0) return null;
  if (isListPlan(plan)) return listSources(plan.intent, results[0] as RecurringResult | FlagResult);
  const result = results[0] as SpendingResult | CountResult;
  if (result.coverage.period_count === 0) return { ...EMPTY, noData: true, rows: [] };
  if (isCountResult(result)) return countSources(plan.categories, result);

  const rows: SourceRow[] = [];
  if (plan.categories === "all_spending") {
    rows.push(row(result.total, "All spending categories", result));
  } else {
    for (const c of plan.categories) {
      const r = result.rows.find((x) => x.category === c);
      if (r) rows.push(row(r, c, result));
    }
  }
  if (result.selection) {
    rows.push(row(result.selection, `Together: ${(result.selection.categories ?? []).join(" + ")}`, result));
  }
  if (result.uncategorized.count > 0) {
    rows.push(row(result.uncategorized, "Not categorized yet (money out)", result));
  }
  return { noData: false, rows, ...EMPTY };
}

/** What a marked number refers to, for its popover and aria-label. */
export function describeSlot(slot: Slot, plan: Plan | null, results: ToolResult[] | null): string {
  if (!results?.[0]) return "Verified";
  if (isListPlan(plan)) {
    const src = sourceRows(plan, results);
    return src ? describeListSlot(slot, src) : "Verified";
  }
  const result = results[0] as SpendingResult | CountResult;
  if (slot.kind === "date_range") {
    return `Period answered · ${formatPeriod(result.period.start, result.period.end)}`;
  }
  if (slot.kind === "date") {
    const { first_date, last_date } = result.coverage;
    return first_date && last_date
      ? `Your imported transactions · ${formatPeriod(first_date, last_date)}`
      : "Your imported transactions";
  }
  const src = sourceRows(plan, results);
  const found = src?.rows.find((r) => r.rowId === slot.row_id);
  if (!found) return "Verified";
  const unit = src!.countsPurchases ? "purchase" : "transaction";
  const n = found.count === 1 ? `1 ${unit}` : `${found.count} ${unit}s`;
  return `${found.what} · ${found.period} · ${n}`;
}

/** "Dining, September 2026": the short source used in a number's aria-label. */
export function slotAriaSource(slot: Slot, plan: Plan | null, results: ToolResult[] | null): string {
  if (!results?.[0]) return "";
  if (isListPlan(plan)) return describeSlot(slot, plan, results);
  const result = results[0] as SpendingResult | CountResult;
  const when = periodName(result.period.start, result.period.end);
  const found = sourceRows(plan, results)?.rows.find((r) => r.rowId === slot.row_id);
  return found ? `${found.what}, ${when}` : when;
}
