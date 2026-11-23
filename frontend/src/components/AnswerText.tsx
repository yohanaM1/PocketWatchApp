import { useState } from "react";

import type { Plan, Slot, ToolResult } from "../api/types";
import { NUMBER_KINDS, segments } from "../lib/segments";
import { describeSlot, slotAriaSource } from "../lib/sources";

interface Props {
  text: string;
  slots: Slot[] | null;
  plan: Plan | null;
  results: ToolResult[] | null;
}

/** Answer text with every verified number marked; each opens a popover naming its source row. */
export function AnswerText({ text, slots, plan, results }: Props) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <p className="answer-text">
      {segments(text, slots).map((seg, i) => {
        if (!seg.slot || !NUMBER_KINDS.has(seg.slot.kind)) return <span key={i}>{seg.text}</span>;
        const source = describeSlot(seg.slot, plan, results);
        return (
          <span key={i} className="num-wrap">
            <button type="button" className="num" aria-label={`${seg.text}, verified: ${slotAriaSource(seg.slot, plan, results)}`}
              aria-expanded={open === i} onClick={() => setOpen(i)} onFocus={() => setOpen(i)}
              onBlur={() => setOpen(null)} onKeyDown={(e) => e.key === "Escape" && setOpen(null)}>
              {seg.text}
            </button>
            {open === i && <span role="tooltip" className="popover">{source}</span>}
          </span>
        );
      })}
    </p>
  );
}
