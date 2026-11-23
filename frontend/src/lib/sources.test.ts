import { describeSlot, formatPeriod, slotAriaSource, sourceRows } from "./sources";
import { answer } from "../test/fixtures";

const rows = (name: string) => {
  const a = answer(name);
  return sourceRows(a.plan, a.final_results);
};

describe("sourceRows", () => {
  it("single category", () => {
    expect(rows("single_dining_sep")).toEqual({
      noData: false,
      rows: [{ rowId: "cat:Dining:2026-09", what: "Dining", period: "Sep 1 – Sep 30, 2026", spending: "$61.87",
        refundsExceeded: false, count: 7 }],
      countsPurchases: false,
      transactions: [],
      truncated: false,
      series: [],
      stopped: 0,
      flags: [],
      dataThrough: null,
    });
  });

  it("a recurring list lists the active series and how many stopped", () => {
    const src = rows("recurring_one_and_one_stopped")!;
    expect(src.series).toEqual([{ rowId: "series:7 ELEVEN 1234:key", merchant: "7 ELEVEN 1234", amount: "$1,250.00",
      next: "Nov 20, 2026" }]);
    expect([src.stopped, src.dataThrough, src.noData]).toEqual([1, "Oct 30, 2026", false]);
  });

  it("a flag list lists the open flags with their labels", () => {
    expect(rows("flags_fixture")!.flags.map((f) => [f.label, f.merchant, f.amount, f.date])).toEqual([
      ["New merchant", "XJ4HOLDINGS", "$225.56", "Oct 24, 2026"],
      ["Possible duplicate", "UBER", "$25.14", "Sep 8, 2026"],
      ["Unusually large", "AMAZON", "$403.43", "Sep 6, 2026"],
    ]);
  });

  it("a list answer for a user with no transactions has no data", () => {
    expect(rows("recurring_no_data")!.noData).toBe(true);
    expect(rows("flags_no_data")!.noData).toBe(true);
  });

  it("multi-category in plan order, then the selection", () => {
    expect(rows("multi_food_oct")!.rows.map((r) => [r.what, r.spending])).toEqual([
      ["Dining", "$57.82"], ["Groceries", "$58.94"], ["Together: Dining + Groceries", "$116.76"],
    ]);
  });

  it("all_spending uses the total row", () => {
    expect(rows("all_spending_sep")!.rows.map((r) => [r.what, r.spending])).toEqual([["All spending categories", "$1,851.45"]]);
  });

  it("uncategorized only when its count is above zero", () => {
    expect(rows("uncat_one")!.rows.at(-1)).toMatchObject({ what: "Not categorized yet (money out)", count: 1, spending: "$12.50" });
    expect(rows("single_dining_sep")!.rows.some((r) => r.what.startsWith("Not categorized"))).toBe(false);
  });

  it("refunds exceeding spending are flagged", () => {
    expect(rows("refund_single")!.rows[0]).toMatchObject({ spending: "-$20.00", refundsExceeded: true });
  });

  it("no-data period", () => {
    expect(rows("no_data_dec")).toMatchObject({ noData: true, rows: [] });
    expect(rows("count_no_data_dec")).toMatchObject({ noData: true, rows: [] });
  });

  it("a purchase count has one count row and no amount", () => {
    expect(rows("count_eat_out_oct")).toMatchObject({
      noData: false,
      countsPurchases: true,
      rows: [{ rowId: "txncount:2026-10:Dining", what: "Dining", period: "Oct 1 – Oct 31, 2026", spending: null, count: 9 }],
    });
    expect(rows("count_all_spending_sep")!.rows[0].what).toBe("All spending categories");
  });

  it("lists the transactions behind a count, refunds and missing merchants included", () => {
    const a = answer("count_eat_out_oct");
    const result = a.final_results![0];
    if (!("count_row" in result)) throw new Error("expected a count result");
    result.rows = [
      { row_id: "txn:1", date: "2026-10-03", amount: "-12.40", merchant: "Chipotle", category: "Dining" },
      { row_id: "txn:2", date: "2026-10-04", amount: "8.00", merchant: null, category: "Dining" },
    ];
    result.truncated = true;
    const src = sourceRows(a.plan, a.final_results)!;
    expect(src.transactions).toEqual([
      { rowId: "txn:1", date: "Oct 3, 2026", merchant: "Chipotle", category: "Dining", amount: "-$12.40" },
      { rowId: "txn:2", date: "Oct 4, 2026", merchant: "Unknown merchant", category: "Dining", amount: "$8.00" },
    ]);
    expect(src.truncated).toBe(true);
  });

  it("nothing for fixed replies and failures", () => {
    expect(rows("fixed_reply")).toBeNull();
    expect(rows("failed")).toBeNull();
  });
});

describe("slot descriptions", () => {
  it("names the source row, period and count", () => {
    const a = answer("single_dining_sep");
    const money = a.slots!.find((s) => s.kind === "money_spending")!;
    expect(describeSlot(money, a.plan, a.final_results)).toBe("Dining · Sep 1 – Sep 30, 2026 · 7 transactions");
    expect(slotAriaSource(money, a.plan, a.final_results)).toBe("Dining, September 2026");
  });

  it("a count slot names purchases, not transactions", () => {
    const a = answer("count_one_shopping");
    const count = a.slots!.find((s) => s.kind === "count")!;
    expect(describeSlot(count, a.plan, a.final_results)).toBe("Shopping · Sep 1 – Sep 30, 2026 · 1 purchase");
  });

  it("formats cross-year and single-day periods", () => {
    expect(formatPeriod("2025-12-15", "2026-01-14")).toBe("Dec 15, 2025 – Jan 14, 2026");
    expect(formatPeriod("2026-10-01", "2026-10-01")).toBe("Oct 1, 2026");
  });
});
