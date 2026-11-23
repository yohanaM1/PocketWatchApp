import { api } from "./client";
import type { CategoryUpdateResult, TransactionPage } from "./types";

export interface TransactionFilters {
  month?: string; // YYYY-MM
  accountId?: number;
  category?: string; // a category name or "uncategorized"
  merchant?: string; // exact merchant name
  review?: boolean; // only rows in the review queue
  counted?: boolean; // only rows counted in spending (not waiting in review)
}

/** GET /transactions, newest first. */
export function listTransactions(
  filters: TransactionFilters,
  page: { limit: number; offset: number },
): Promise<TransactionPage> {
  const params = new URLSearchParams({ limit: String(page.limit), offset: String(page.offset) });
  if (filters.month) params.set("month", filters.month);
  if (filters.accountId !== undefined) params.set("account_id", String(filters.accountId));
  if (filters.category) params.set("category", filters.category);
  if (filters.merchant) params.set("merchant", filters.merchant);
  if (filters.review) params.set("review", "true");
  if (filters.counted) params.set("counted", "true");
  return api<TransactionPage>(`/transactions?${params.toString()}`);
}

/** PATCH /transactions/{id}. applyToMerchant also saves a correction and updates the merchant's other rows. */
export function setCategory(id: number, category: string, applyToMerchant = false): Promise<CategoryUpdateResult> {
  return api<CategoryUpdateResult>(`/transactions/${id}`, {
    method: "PATCH",
    json: { category, apply_to_merchant: applyToMerchant },
  });
}

export function listCategories(): Promise<string[]> {
  return api<string[]>("/categories");
}
