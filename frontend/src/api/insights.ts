import { api } from "./client";
import type { Flag, FlagPage, FlagStatus, RecurringPage, TransactionOut } from "./types";

/** GET /recurring: active series first (largest first), then stopped ones. */
export function listRecurring(): Promise<RecurringPage> {
  return api<RecurringPage>("/recurring");
}

/** GET /recurring/transactions: one series' charges, newest first. 404 once the key is gone (refetch the list). */
export function recurringTransactions(key: string): Promise<TransactionOut[]> {
  return api<TransactionOut[]>(`/recurring/transactions?${new URLSearchParams({ key }).toString()}`);
}

/** GET /anomalies: flags with one status (or all), newest transaction first, plus the open count. */
export function listFlags(status: FlagStatus | "all" = "open"): Promise<FlagPage> {
  return api<FlagPage>(`/anomalies?${new URLSearchParams({ status }).toString()}`);
}

/** PATCH /anomalies/{id}: dismiss, confirm or reopen a flag. */
export function setFlagStatus(id: number, status: FlagStatus): Promise<Flag> {
  return api<Flag>(`/anomalies/${id}`, { method: "PATCH", json: { status } });
}
