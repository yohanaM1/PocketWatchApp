import { api } from "./client";
import type { BudgetPage, BudgetRow } from "./types";

/** GET /budgets: one row per spending category for a month. Without a month, the month of the latest transaction. */
export function getBudgets(month?: string): Promise<BudgetPage> {
  return api<BudgetPage>(month ? `/budgets?${new URLSearchParams({ month }).toString()}` : "/budgets");
}

/** PUT /budgets/{month}/{category}: create or replace one budget. */
export function setBudget(month: string, category: string, limit: string): Promise<BudgetRow> {
  return api<BudgetRow>(`/budgets/${month}/${encodeURIComponent(category)}`, { method: "PUT", json: { limit } });
}

/** DELETE /budgets/{id}. */
export function deleteBudget(id: number): Promise<void> {
  return api<void>(`/budgets/${id}`, { method: "DELETE" });
}

/** POST /budgets/copy: copy one month's budgets into another, skipping categories it already has. */
export function copyBudgets(fromMonth: string, toMonth: string): Promise<{ copied: number; skipped: number }> {
  return api(`/budgets/copy`, { method: "POST", json: { from_month: fromMonth, to_month: toMonth } });
}
