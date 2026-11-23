import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import type { BudgetPage, BudgetRow, TransactionOut } from "../api/types";
import { changeText, limitError, monthLabel, shiftMonth } from "./BudgetsPage";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const CATEGORIES = ["Dining", "Groceries", "Rent", "Utilities", "Transport", "Subscriptions", "Shopping",
  "Entertainment", "Education", "Health", "Other"];

function row(category: string, overrides: Partial<BudgetRow> = {}): BudgetRow {
  return {
    category, budget_id: null, limit: null, spent: "0.00", left: null, percent: null, status: "none",
    previous_spent: "0.00", ...overrides,
  };
}

const OCTOBER_ROWS: Record<string, Partial<BudgetRow>> = {
  Dining: { budget_id: 7, limit: "80.00", spent: "82.54", left: "-2.54", percent: 103, status: "over", previous_spent: "61.87" },
  Groceries: { budget_id: 8, limit: "120.00", spent: "105.16", left: "14.84", percent: 88, status: "near", previous_spent: "161.39" },
  Rent: { budget_id: 9, limit: "925.00", spent: "925.00", left: "0.00", percent: 100, status: "near", previous_spent: "925.00" },
  Other: { spent: "225.56", previous_spent: "0.00" },
};

function page(overrides: Partial<BudgetPage> = {}): BudgetPage {
  return {
    month: "2026-10", as_of: "2026-10-30", max_month: "2026-11", budgeted: "1125.00", spent: "1112.70", left: "12.30",
    over_count: 1, budget_count: 3, rows: CATEGORIES.map((c) => row(c, OCTOBER_ROWS[c])),
    pending_review_count: 3, previous_month_count: 0, ...overrides,
  };
}

function txn(id: number, date: string, merchant: string, amount: string): TransactionOut {
  return {
    id, account_id: 1, account_name: "Checking", date, amount, merchant, description: merchant.toUpperCase(),
    category: "Dining", category_source: "user", category_confidence: 1, in_review: false,
  };
}

const DINING = [
  txn(1, "2026-10-06", "Starbucks", "-4.76"), txn(2, "2026-10-06", "Panera Bread", "-12.12"),
  txn(3, "2026-10-07", "Starbucks", "-6.75"), txn(4, "2026-10-10", "Starbucks", "-6.73"),
  txn(5, "2026-10-11", "Starbucks", "-5.06"), txn(6, "2026-10-14", "UMD Dining", "-11.06"),
  txn(7, "2026-10-17", "UMD Dining", "-11.34"), txn(8, "2026-10-24", "Uber Eats", "-17.64"),
  txn(9, "2026-10-26", "Dunkin", "-7.08"),
];

function stubFetch(handler: Handler = () => undefined) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    const method = init.method ?? "GET";
    if (method === "GET" && (url === "/api/budgets" || url.startsWith("/api/budgets?"))) return json(200, page());
    if (url.startsWith("/api/anomalies?")) return json(200, { items: [], open_count: 0 });
    if (url.startsWith("/api/transactions?")) {
      return json(200, { items: [], total: 0, money_out: "0.00", money_in: "0.00", uncategorized_count: 0 });
    }
    return json(404, { detail: "unexpected" });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const calls = (fn: ReturnType<typeof stubFetch>, method: string) =>
  fn.mock.calls.filter(([, init]) => (init?.method ?? "GET") === method).map(([url, init]) => [url, init?.body]);

function renderPage() {
  sessionStorage.setItem("pocketwatch.token", "t0k3n");
  return render(<MemoryRouter initialEntries={["/budgets"]}><App /></MemoryRouter>);
}

const rowOf = (category: string) => screen.getByRole("button", { name: category }).closest("tr") as HTMLElement;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("helpers", () => {
  it("labels and shifts months across years", () => {
    expect(monthLabel("2026-10")).toBe("October 2026");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
  });

  it("checks limits like the API does", () => {
    expect(limitError("150")).toBeNull();
    expect(limitError("$1,250.50")).toBeNull();
    expect(limitError("0")).toMatch(/above \$0/);
    expect(limitError("10.001")).toMatch(/above \$0/);
    expect(limitError("2000000")).toMatch(/up to/);
  });

  it("describes the change from last month, or a dash when it wasn't imported", () => {
    expect(changeText("61.87", "82.54")).toBe("+$20.67");
    expect(changeText("161.39", "105.16")).toBe("−$56.23");
    expect(changeText("925.00", "925.00")).toBe("same");
    expect(changeText(null, "82.54")).toBe("—");
  });
});

describe("BudgetsPage", () => {
  it("shows the dial banner and every spending category in a fixed order", async () => {
    stubFetch();
    renderPage();

    const banner = await screen.findByRole("region", { name: "Month total" });
    expect(within(banner).getByRole("img", { name: "99% of the month's budget spent" })).toBeInTheDocument();
    expect(within(banner).getByText("$1,112.70")).toBeInTheDocument();
    expect(within(banner).getByText("Dining")).toBeInTheDocument(); // the over list
    expect(within(banner).getByText(/3 transactions are waiting in review/)).toBeInTheDocument();
    expect(within(banner).getByRole("link", { name: "Review them" })).toHaveAttribute("href", "/review");

    const names = screen.getAllByRole("rowheader").map((h) => h.textContent?.replace("›", "").trim());
    expect(names).toEqual([...CATEGORIES, "Total budgeted"]);

    const dining = within(rowOf("Dining"));
    expect(dining.getByRole("textbox", { name: "Dining budget" })).toHaveValue("80.00");
    expect(dining.getByText("$2.54 over")).toBeInTheDocument();
    expect(dining.getByText("103%")).toBeInTheDocument();
    expect(dining.getByText("+$20.67")).toBeInTheDocument();
    expect(within(rowOf("Other")).getByRole("textbox", { name: "Other budget" })).toHaveValue("");
    expect(within(rowOf("Rent")).getByText("same")).toBeInTheDocument();
  });

  it("saves on Enter, says so, and rejects a bad amount without saving", async () => {
    const fetch = stubFetch((_url, init) => (init.method === "PUT"
      ? json(200, row("Dining", { ...OCTOBER_ROWS.Dining, limit: "250.00" })) : undefined));
    renderPage();
    const user = userEvent.setup();

    const input = await screen.findByRole("textbox", { name: "Dining budget" });
    await user.clear(input);
    await user.type(input, "0{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Enter an amount above $0");
    expect(calls(fetch, "PUT")).toEqual([]);

    await user.clear(input);
    await user.type(input, "250{Enter}");
    await waitFor(() => expect(calls(fetch, "PUT")).toEqual([["/api/budgets/2026-10/Dining", '{"limit":"250"}']]));
    expect(await within(rowOf("Dining")).findByText("Saved")).toBeInTheDocument();
  });

  it("saves when you leave the cell, and Esc puts the old value back without saving", async () => {
    const fetch = stubFetch((_url, init) => (init.method === "PUT" ? json(200, row("Other", { limit: "50.00" })) : undefined));
    renderPage();
    const user = userEvent.setup();

    const dining = await screen.findByRole("textbox", { name: "Dining budget" });
    await user.clear(dining);
    await user.type(dining, "999{Escape}");
    expect(dining).toHaveValue("80.00");
    expect(calls(fetch, "PUT")).toEqual([]);

    const other = screen.getByRole("textbox", { name: "Other budget" });
    await user.type(other, "50");
    await user.tab();
    await waitFor(() => expect(calls(fetch, "PUT")).toEqual([["/api/budgets/2026-10/Other", '{"limit":"50"}']]));
  });

  it("removes a budget when the cell is cleared, with undo", async () => {
    const fetch = stubFetch((_url, init) => {
      if (init.method === "DELETE") return Promise.resolve(new Response(null, { status: 204 }));
      if (init.method === "PUT") return json(200, row("Dining", OCTOBER_ROWS.Dining));
      return undefined;
    });
    renderPage();
    const user = userEvent.setup();

    const input = await screen.findByRole("textbox", { name: "Dining budget" });
    await user.clear(input);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls(fetch, "DELETE")).toEqual([["/api/budgets/7", undefined]]));
    await user.click(await within(rowOf("Dining")).findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(calls(fetch, "PUT")).toEqual([["/api/budgets/2026-10/Dining", '{"limit":"80.00"}']]));
  });

  it("opens a category to show its largest counted purchases and last month", async () => {
    const fetch = stubFetch((url) => (url.startsWith("/api/transactions?") && url.includes("category=Dining")
      ? json(200, { items: DINING, total: 9, money_out: "82.54", money_in: "0.00", uncategorized_count: 0 })
      : undefined));
    renderPage();
    const user = userEvent.setup();

    const toggle = await screen.findByRole("button", { name: "Dining" });
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(fetch.mock.calls.map(([url]) => url)).toContain(
      "/api/transactions?limit=200&offset=0&month=2026-10&category=Dining&counted=true");

    const list = await screen.findByRole("list", { name: "Dining purchases" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(8);
    expect(items[0]).toHaveTextContent("Uber Eats");
    expect(screen.getByText("8 largest of 9 purchases")).toBeInTheDocument();
    expect(screen.getByText("Up $20.67 (33%)")).toBeInTheDocument();
    expect(screen.getByText("Uber Eats · $17.64")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See all 9 in Transactions" }))
      .toHaveAttribute("href", "/transactions?month=2026-10&category=Dining");

    await user.click(toggle);
    expect(screen.queryByRole("list", { name: "Dining purchases" })).not.toBeInTheDocument();
  });

  it("shows a dash for last month when it wasn't imported", async () => {
    stubFetch((url, init) => ((init.method ?? "GET") === "GET" && url.startsWith("/api/budgets")
      ? json(200, page({ rows: CATEGORIES.map((c) => row(c, { ...OCTOBER_ROWS[c], previous_spent: null })) }))
      : undefined));
    renderPage();
    expect(await within(await screen.findByRole("table")).findAllByText("—")).not.toHaveLength(0);
    expect(within(rowOf("Dining")).getAllByText("—")).toHaveLength(1);
  });

  it("fills blank categories from last month", async () => {
    const fetch = stubFetch((url, init) => {
      if ((init.method ?? "GET") === "GET" && url.startsWith("/api/budgets")) {
        return json(200, page({ month: "2026-11", budget_count: 0, budgeted: "0.00", spent: "0.00", left: "0.00",
          over_count: 0, previous_month_count: 5, rows: CATEGORIES.map((c) => row(c)) }));
      }
      if (url === "/api/budgets/copy") return json(200, { copied: 5, skipped: 0 });
      return undefined;
    });
    renderPage();
    const user = userEvent.setup();

    expect(await screen.findByText("No budgets for November 2026")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next month" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Fill blanks from October" }));
    await waitFor(() => expect(calls(fetch, "POST")).toEqual([
      ["/api/budgets/copy", '{"from_month":"2026-10","to_month":"2026-11"}'],
    ]));
  });

  it("goes to the previous month", async () => {
    const fetch = stubFetch();
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Previous month" }));
    await waitFor(() => expect(fetch.mock.calls.map(([url]) => url)).toContain("/api/budgets?month=2026-09"));
  });

  it("says when budgets can't load and retries", async () => {
    let fail = true;
    stubFetch((url) => (url === "/api/budgets" && fail ? json(500, { detail: "boom" }) : undefined));
    renderPage();
    const user = userEvent.setup();

    expect(await screen.findByText("Couldn't load your budgets.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("region", { name: "Month total" })).toBeInTheDocument();
  });
});
