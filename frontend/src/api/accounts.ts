import { api } from "./client";
import type { AccountOut, AccountType, ImportResult } from "./types";

/** GET /accounts: the user's accounts, oldest first (registration creates "Checking"). */
export function listAccounts(): Promise<AccountOut[]> {
  return api<AccountOut[]>("/accounts");
}

export function createAccount(name: string, type: AccountType): Promise<AccountOut> {
  return api<AccountOut>("/accounts", { method: "POST", json: { name, type } });
}

/** POST /accounts/{id}/imports. All-or-nothing: a 422 carries the per-line errors. */
export function importCsv(accountId: number, file: File): Promise<ImportResult> {
  const form = new FormData();
  form.append("file", file);
  return api<ImportResult>(`/accounts/${accountId}/imports`, { method: "POST", multipart: form });
}
