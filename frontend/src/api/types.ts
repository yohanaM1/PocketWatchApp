// TypeScript mirror of the backend's QuestionOut (app/agent/router.py) and the group_by_category
// and get_transactions results (app/agent/tools/). The only shapes the UI reads.

export type Status = "verified" | "unverified" | "failed";
export type SlotKind =
  | "money_spending"
  | "money_refund"
  | "count"
  | "date_range"
  | "date"
  | "category"
  | "period_label"
  | "phrase"
  | "merchant" // list answers: a merchant name from the user's own data
  | "money_charge" // list answers: a money-out amount, shown positive
  | "flag_label"; // list answers: "Possible duplicate", "Unusually large", "New merchant"

/** start/end are Python string indices: Unicode code points, not UTF-16 units. */
export interface Slot {
  start: number;
  end: number;
  row_id: string;
  field: string;
  kind: SlotKind;
}

export interface ResolvedPeriod {
  start: string;
  end: string;
  partial: boolean;
  key: string;
}

export type ListIntent = "recurring_list" | "flag_list";

export type Plan =
  | { intent: "no_data"; label: string; source: "code" | "llm" }
  | { intent: ListIntent; as_of: string; data_through: string | null }
  | {
      intent: "category_total" | "transaction_count";
      categories: string[] | "all_spending";
      category_phrase: string;
      as_of: string;
      resolved: ResolvedPeriod;
    };

export interface AmountRow {
  row_id: string;
  net_amount: string;
  spending: string;
  count: number;
  category?: string;
  categories?: string[];
  rank?: number;
}

interface ResultPeriod {
  start: string;
  end: string;
  partial: boolean;
  label: string;
  key: string;
}

interface Coverage {
  row_id: "coverage";
  first_date: string | null;
  last_date: string | null;
  period_count: number;
}

/** group_by_category, behind a category_total answer. */
export interface SpendingResult {
  period: ResultPeriod;
  rows: AmountRow[];
  total: AmountRow;
  uncategorized: AmountRow;
  selection?: AmountRow;
  coverage: Coverage;
}

export interface TransactionRow {
  row_id: string;
  date: string;
  amount: string;
  merchant: string | null;
  category: string | null;
}

/** get_transactions, behind a transaction_count answer. `rows` is capped; `count_row` holds the true counts. */
export interface CountResult {
  period: ResultPeriod;
  count_row: { row_id: string; total_count: number; money_out_count: number };
  rows: TransactionRow[];
  truncated: boolean;
  coverage: Coverage;
}

interface ListCoverage {
  row_id: "coverage";
  last_date: string | null;
  count: number; // the user's transactions
}

/** find_recurring, behind a recurring_list answer: active series, largest first. */
export interface RecurringResult {
  rows: { row_id: string; key: string; merchant: string; amount: string; next_expected: string }[];
  total: { row_id: "rectotal"; amount: string; count: number };
  stopped: { row_id: "stopped"; count: number };
  coverage: ListCoverage;
}

/** detect_anomalies, behind a flag_list answer: open flags, newest first. */
export interface FlagResult {
  rows: { row_id: string; kind: "duplicate" | "large_charge" | "unfamiliar_merchant"; merchant: string; amount: string;
    date: string }[];
  total: { row_id: "flagcount"; count: number };
  coverage: ListCoverage;
}

export type ToolResult = SpendingResult | CountResult | RecurringResult | FlagResult;

export function isCountResult(r: ToolResult): r is CountResult {
  return "count_row" in r;
}

export function isListPlan(p: Plan | null): p is Extract<Plan, { intent: ListIntent }> {
  return p !== null && (p.intent === "recurring_list" || p.intent === "flag_list");
}

export interface QuestionOut {
  id: number;
  question: string;
  status: Status;
  answer: string | null;
  extra: string | null;
  plan: Plan | null;
  slots: Slot[] | null;
  final_results: ToolResult[] | null;
  attempts: number;
  as_of: string | null;
  created_at: string;
}

// Mirrors of app/schemas.py (accounts, imports, transactions).

export type AccountType = "checking" | "savings" | "credit";

export interface AccountOut {
  id: number;
  name: string;
  type: AccountType;
}

export interface ImportResult {
  total_rows: number;
  imported: number;
  skipped_duplicates: number;
  categorized?: number;
  needs_review?: number;
}

/** The 422 detail when any CSV row is invalid (nothing is imported). */
export interface ImportErrorDetail {
  message: string;
  errors: { line: number; message: string }[];
}

export type CategorySource = "correction" | "llm" | "user";

export interface TransactionOut {
  id: number;
  account_id: number;
  account_name: string;
  date: string;
  amount: string; // 2-decimal string; negative = money out
  merchant: string | null;
  description: string;
  category: string | null;
  category_source: CategorySource | null;
  category_confidence: number | null; // 1.0 for user/correction; the LLM's own score for an llm guess
  in_review: boolean; // waiting in the review queue: `category` is only a guess, not counted in budgets or answers
}

export interface TransactionPage {
  items: TransactionOut[];
  total: number;
  // totals over every page of the filtered set, as positive 2-decimal strings
  money_out: string;
  money_in: string;
  uncategorized_count: number;
  review_count: number; // uncategorized plus low-confidence guesses, as on the Review page
}

export interface CategoryUpdateResult {
  transaction: TransactionOut;
  also_updated: number;
  same_merchant_count: number;
}

// Mirrors of app/schemas.py (review queue).

export interface ReviewGuess {
  category: string;
  count: number; // queued rows in the group with this guess
  confidence: number | null; // the lowest among them
}

export type MixedReason = "known_mixed" | "guesses_differ" | "history_differs";

export interface ReviewGroup {
  merchant: string | null;
  count: number;
  reason: "uncategorized" | "low_confidence";
  guesses: ReviewGuess[]; // most common first
  suggestion: string | null;
  mixed: boolean; // handle one transaction at a time; don't remember by default
  mixed_reason: MixedReason | null;
  money_out: string; // positive 2-decimal strings
  money_in: string;
  first_date: string;
  last_date: string;
  examples: TransactionOut[]; // up to 3, newest first
}

export interface ReviewPage {
  groups: ReviewGroup[];
  total_groups: number;
  total_transactions: number;
}

export interface ReviewResolveResult {
  updated: number;
  also_updated: number;
}

// Recurring payments and flags (GET /recurring, GET /anomalies)
export interface RecurringSeries {
  key: string; // stable across re-detection; use it, not a database id
  merchant: string;
  category: string | null;
  amount: string; // the current amount, negative = money out
  previous_amount: string | null; // before the most recent price change
  amount_changed_on: string | null;
  interval_days: number;
  first_date: string;
  last_date: string;
  next_expected: string | null; // null for a stopped series
  charge_count: number;
  active: boolean;
}

export interface RecurringPage {
  as_of: string | null; // the latest transaction date
  monthly_total: string; // negative
  active_count: number;
  series: RecurringSeries[];
}

export type FlagKind = "duplicate" | "large_charge" | "unfamiliar_merchant";
export type FlagStatus = "open" | "dismissed" | "confirmed";

export interface Flag {
  id: number;
  kind: FlagKind;
  reason: string;
  status: FlagStatus;
  created_at: string;
  transaction: TransactionOut;
}

export interface FlagPage {
  items: Flag[];
  open_count: number;
}

export type BudgetStatus = "ok" | "near" | "over" | "none"; // none = no budget

export interface BudgetRow {
  category: string;
  budget_id: number | null;
  limit: string | null;
  spent: string; // review-queue guesses aren't counted
  left: string | null; // limit - spent, negative when over
  percent: number | null; // can pass 100, or be negative after refunds
  status: BudgetStatus;
  previous_spent: string | null; // null when nothing was imported for the month before
}

export interface BudgetPage {
  month: string; // YYYY-MM
  as_of: string | null; // the latest transaction date
  max_month: string; // the › arrow stops here
  budgeted: string; // totals cover budgeted categories only
  spent: string;
  left: string;
  over_count: number;
  budget_count: number;
  rows: BudgetRow[]; // all 11 spending categories, fixed order
  pending_review_count: number;
  previous_month_count: number;
}

export interface SavedRule {
  id: number;
  merchant: string;
  category: string;
  transaction_count: number; // your transactions from the merchant currently in the rule's category
  mixed: boolean; // a multi-purpose merchant, where one category is risky
}
