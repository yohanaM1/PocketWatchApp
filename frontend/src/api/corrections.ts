import { api } from "./client";
import type { SavedRule } from "./types";

/** GET /corrections: your saved rules, A→Z by merchant. */
export function listRules(): Promise<SavedRule[]> {
  return api<SavedRule[]>("/corrections");
}

/** PATCH /corrections/{id}: change a rule's category; its transactions move with it. */
export function changeRule(id: number, category: string): Promise<{ rule: SavedRule; updated: number }> {
  return api(`/corrections/${id}`, { method: "PATCH", json: { category } });
}

/** DELETE /corrections/{id}: remove a rule; sendToReview also makes its transactions uncategorized. */
export function removeRule(id: number, sendToReview: boolean): Promise<{ sent_to_review: number }> {
  return api(`/corrections/${id}?${new URLSearchParams({ send_to_review: String(sendToReview) }).toString()}`,
    { method: "DELETE" });
}
