// Tells the nav badge to recount the review queue after something changed it without a route change
// (a resolve, a category edit, an import).

const EVENT = "pocketwatch:review-changed";

export function notifyReviewChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** Subscribe; returns the unsubscribe function. */
export function onReviewChanged(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
