// Tells the nav badge to recount open flags after something changed them without a route change
// (a dismiss or confirm on the Flags page). Imports and category edits already send the review event,
// which the flags badge listens to as well, since detection re-runs after both.

const EVENT = "pocketwatch:flags-changed";

export function notifyFlagsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** Subscribe; returns the unsubscribe function. */
export function onFlagsChanged(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
