import { isListPlan } from "../api/types";
import { segments } from "./segments";
import { ANSWERS, answer } from "../test/fixtures";

describe("segments", () => {
  const withText = Object.entries(ANSWERS).filter(([, a]) => a.answer && a.slots);

  it.each(withText)("%s: rebuilds the text and each slot's segment is the slot's text", (_, a) => {
    const segs = segments(a.answer!, a.slots);
    expect(segs.map((s) => s.text).join("")).toBe(a.answer);
    const slotted = segs.filter((s) => s.slot);
    expect(slotted).toHaveLength(a.slots!.length);
    for (const seg of slotted) {
      const s = seg.slot!;
      if (s.kind === "money_spending" || s.kind === "money_refund" || s.kind === "money_charge") {
        expect(seg.text).toMatch(/^\$\d{1,3}(,\d{3})*\.\d{2}$/);
      }
      if (s.kind === "count") expect(seg.text).toMatch(/^\d+$/);
      if (s.kind === "date" || s.kind === "date_range") expect(seg.text).toMatch(/^[A-Z][a-z]{2} \d/);
      const plan = a.plan;
      if (!plan || plan.intent === "no_data" || isListPlan(plan)) continue;
      if (s.kind === "phrase") expect(seg.text).toBe(plan.category_phrase);
      if (s.kind === "category" && s.row_id === "plan" && plan.categories !== "all_spending") {
        expect(seg.text).toBe(plan.categories[Number(s.field.split(".")[1])]);
      }
    }
  });

  it("indexes by code point, which text.slice would get wrong after an emoji", () => {
    const a = answer("emoji_phrase");
    const last = a.slots![a.slots!.length - 1];
    const seg = segments(a.answer!, a.slots).find((s) => s.slot === last)!;
    expect(seg.text).toBe("Sep 1–30, 2026");
    expect(a.answer!.slice(last.start, last.end)).not.toBe("Sep 1–30, 2026");
  });

  it.each([
    ["overlapping", [{ start: 0, end: 5 }, { start: 3, end: 8 }]],
    ["out of range", [{ start: 0, end: 999 }]],
    ["unsorted", [{ start: 10, end: 12 }, { start: 0, end: 3 }]],
    ["empty", [{ start: 4, end: 4 }]],
  ])("%s slots give one plain segment without throwing", (_, spans) => {
    const slots = spans.map((s) => ({ ...s, row_id: "x", field: "y", kind: "count" as const }));
    expect(segments("You spent $1.00 on it.", slots)).toEqual([{ text: "You spent $1.00 on it." }]);
  });
});
