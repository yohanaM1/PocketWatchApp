import { api } from "./client";
import type { ReviewPage, ReviewResolveResult } from "./types";

/** GET /review: queued transactions grouped by merchant, biggest groups first. Pages over groups. */
export function listReview(page: { limit: number; offset: number }): Promise<ReviewPage> {
  const params = new URLSearchParams({ limit: String(page.limit), offset: String(page.offset) });
  return api<ReviewPage>(`/review?${params.toString()}`);
}

/** POST /review/resolve: set every queued row from `merchant` to `category`; `remember` also saves a correction. */
export function resolveGroup(merchant: string | null, category: string, remember: boolean): Promise<ReviewResolveResult> {
  return api<ReviewResolveResult>("/review/resolve", { method: "POST", json: { merchant, category, remember } });
}
