import { api } from "./client";
import type { QuestionOut } from "./types";

/** POST /questions. Always 201 when it reaches the server; the outcome is in `status`. */
export function ask(question: string, signal?: AbortSignal): Promise<QuestionOut> {
  return api<QuestionOut>("/questions", { method: "POST", json: { question }, signal });
}

/** GET /questions, newest first. Never re-asks anything. */
export function history(opts: { limit?: number; beforeId?: number } = {}): Promise<QuestionOut[]> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.beforeId !== undefined) params.set("before_id", String(opts.beforeId));
  const qs = params.toString();
  return api<QuestionOut[]>(`/questions${qs ? `?${qs}` : ""}`);
}

export function getQuestion(id: number): Promise<QuestionOut> {
  return api<QuestionOut>(`/questions/${id}`);
}
