import type { Slot, SlotKind } from "../api/types";

export interface Segment {
  text: string;
  slot?: Slot;
}

export const NUMBER_KINDS: ReadonlySet<SlotKind> = new Set(["money_spending", "money_refund", "money_charge", "count",
  "date_range", "date"]);

/**
 * Split answer text at slot boundaries. Slot offsets are Python indices (code points), so this
 * works on Array.from(text), never text.slice. Invalid slots (overlapping, out of range, unsorted)
 * give one plain segment: the server already verified the answer, highlights are presentation only.
 */
export function segments(text: string, slots: Slot[] | null | undefined): Segment[] {
  const cps = Array.from(text);
  if (!slots || slots.length === 0) return [{ text }];
  let prevEnd = 0;
  for (const s of slots) {
    const ok =
      Number.isInteger(s.start) && Number.isInteger(s.end) && s.start >= prevEnd && s.start < s.end && s.end <= cps.length;
    if (!ok) return [{ text }];
    prevEnd = s.end;
  }

  const out: Segment[] = [];
  let pos = 0;
  for (const s of slots) {
    if (s.start > pos) out.push({ text: cps.slice(pos, s.start).join("") });
    out.push({ text: cps.slice(s.start, s.end).join(""), slot: s });
    pos = s.end;
  }
  if (pos < cps.length) out.push({ text: cps.slice(pos).join("") });
  return out;
}
