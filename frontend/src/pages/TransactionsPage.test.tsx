import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { App } from "../App";
import type { TransactionOut } from "../api/types";
import { formatAmount, formatDate } from "./TransactionsPage";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

/** A GET /transactions body; totals default to zero. */
const page = (items: TransactionOut[], total = items.length, totals = {}) => ({
  items, total, money_out: "0.00", money_in: "0.00", uncategorized_count: 0,
  review_count: items.filter((t) => t.in_review).length, ...totals,
});

/** in_review as the API decides it: uncategorized, or an LLM guess below the 0.75 threshold. */
const inReview = (t: Omit<TransactionOut, "in_review">) =>
  t.category === null || (t.category_source === "llm" && (t.category_confidence ?? 0) < 0.75);


const CATEGORIES = ["Dining", "Groceries", "Rent", "Other"];
const ACCOUNTS = [{ id: 1, name: "Checking", type: "checking" }];

function txn(id: number, overrides: Partial<TransactionOut> = {}): TransactionOut {
  const t = {
    id, account_id: 1, account_name: "Checking", date: "2026-10-05", amount: "-11.38", merchant: "Chipotle",
    description: "CHIPOTLE MEXICAN GRILL COLLEGE PARK MD", category: null, category_source: null, category_confidence: null, ...overrides,
  };
  return { ...t, in_review: overrides.in_review ?? inReview(t) };
}

/** Stub fetch: `handler` answers first; accounts, categories and an empty list are the defaults. */
function stubFetch(handler: Handler = () => undefined) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    if (url === "/api/accounts") return json(200, ACCOUNTS);
    if (url === "/api/categories") return json(200, CATEGORIES);
    if (url.startsWith("/api/transactions?")) return json(200, page([]));
    return json(404, { detail: "unexpected" });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function Location() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderPage(entry = "/transactions") {
  sessionStorage.setItem("pocketwatch.token", "t0k3n");
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
      <Location />
    </MemoryRouter>,
  );
}

// List requests from the page; the nav badge's review=true count is left out.
const listCalls = (fn: ReturnType<typeof stubFetch>) =>
  fn.mock.calls.map(([url]) => url as string)
    .filter((u) => u.startsWith("/api/transactions?") && !u.includes("review=true"));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("formatting", () => {
  it("formats dates without a timezone shift and amounts without floats", () => {
    expect(formatDate("2026-10-01")).toBe("Oct 1, 2026");
    expect(formatAmount("-1234.50")).toBe("−$1,234.50");
    expect(formatAmount("200.00")).toBe("+$200.00");
  });
});

describe("TransactionsPage", () => {
  it("lists transactions with the empty-category placeholder", async () => {
    stubFetch((url) => (url.startsWith("/api/transactions?")
      ? json(200, page([txn(1), txn(2, { amount: "400.21", merchant: "UMD Payroll", category: "Income" })], 2,
        { money_out: "1851.45", money_in: "400.21", uncategorized_count: 1 }))
      : undefined));
    renderPage();
    expect(await screen.findByText("Showing 2 of 2")).toBeInTheDocument();
    expect(screen.getByText("−$11.38")).toBeInTheDocument();
    expect(screen.getByText("+$400.21")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category for Chipotle on Oct 5, 2026" })).toHaveValue("");
    const tiles = screen.getByRole("region", { name: "Totals" });
    expect(tiles).toHaveTextContent("Money out$1,851.45across all months");
    expect(tiles).toHaveTextContent("Money in$400.21");
    expect(tiles).toHaveTextContent("Need a category1Review it");
  });

  it("the need-a-category tile opens the review queue", async () => {
    stubFetch((url) => (url.startsWith("/api/transactions?")
      ? json(200, page([txn(1)], 1, { uncategorized_count: 1, review_count: 4 }))
      : undefined));
    renderPage("/transactions?month=2026-09");
    const tiles = await screen.findByRole("region", { name: "Totals" });
    expect(tiles).toHaveTextContent("in September 2026");
    expect(tiles).toHaveTextContent("Need a category4"); // guesses waiting in review count, not just uncategorized
    await userEvent.click(screen.getByRole("button", { name: "Review them" }));
    expect(screen.getByTestId("location")).toHaveTextContent(/^\/review$/);
  });

  it("shows an unconfirmed guess as a guess, and confirming it needs no undo", async () => {
    stubFetch((url, init) => {
      if (url === "/api/transactions/1" && init.method === "PATCH") {
        return json(200, { transaction: txn(1, { category: "Dining", category_source: "user", category_confidence: 1 }),
          also_updated: 0, same_merchant_count: 0 });
      }
      if (url.startsWith("/api/transactions?")) {
        return json(200, page([txn(1, { category: "Dining", category_source: "llm", category_confidence: 0.55 }),
          txn(2, { category: "Dining", category_source: "llm", category_confidence: 0.92 })]));
      }
      return undefined;
    });
    renderPage();
    const [guessed, confident] = await screen.findAllByRole("combobox", { name: /Category for Chipotle/ });
    expect(guessed).toHaveClass("guess");
    expect(confident).not.toHaveClass("guess");
    expect(screen.getAllByText(/Guess · 55% · waiting in review/)).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Totals" })).toHaveTextContent("Need a category1");

    await userEvent.click(screen.getByRole("button", { name: "Confirm Chipotle as Dining" }));
    await waitFor(() => expect(screen.queryByText(/Guess · 55%/)).not.toBeInTheDocument());
    expect(screen.queryByText(/^Was /)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Totals" })).toHaveTextContent("Need a category0");
  });

  it("shows the empty state when there are no transactions", async () => {
    stubFetch();
    renderPage();
    expect(await screen.findByText("No transactions yet. Import a CSV above to get started.")).toBeInTheDocument();
  });

  it("keeps filters in the URL and refetches with them", async () => {
    const fetchFn = stubFetch();
    renderPage();
    await screen.findByText(/No transactions yet/);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Category" }), "uncategorized");
    await waitFor(() => expect(listCalls(fetchFn).at(-1)).toContain("category=uncategorized"));
    expect(screen.getByTestId("location")).toHaveTextContent("/transactions?category=uncategorized");
    expect(await screen.findByText("No transactions match these filters.")).toBeInTheDocument();
  });

  it("reads filters from the URL on load", async () => {
    const fetchFn = stubFetch();
    renderPage("/transactions?month=2026-09&account=1");
    await waitFor(() => expect(listCalls(fetchFn)).toHaveLength(1));
    expect(listCalls(fetchFn)[0]).toContain("month=2026-09");
    expect(listCalls(fetchFn)[0]).toContain("account_id=1");
  });

  it("shows an error with retry when the list fails", async () => {
    let fail = true;
    stubFetch((url) => (url.startsWith("/api/transactions?") && fail ? json(500, { detail: "boom" }) : undefined));
    renderPage();
    expect(await screen.findByText("Couldn't load your transactions.")).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument();
  });

  it("uploads a CSV into the selected account and reports the result", async () => {
    const fetchFn = stubFetch((url, init) => (url === "/api/accounts/1/imports" && init.method === "POST"
      ? json(200, { total_rows: 63, imported: 60, skipped_duplicates: 3 })
      : undefined));
    renderPage();
    await screen.findAllByRole("option", { name: "Checking" });
    const file = new File(["date,description,amount\n"], "bank.csv", { type: "text/csv" });
    await userEvent.upload(screen.getByLabelText("CSV file"), file);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByText("Imported 60 of 63 rows · skipped 3 already imported.")).toBeInTheDocument();
    const post = fetchFn.mock.calls.find(([url]) => url === "/api/accounts/1/imports")!;
    expect(post[1]!.body).toBeInstanceOf(FormData);
    await waitFor(() => expect(listCalls(fetchFn).length).toBeGreaterThan(1)); // the list reloads
  });

  it("lists each bad line when an import is rejected", async () => {
    stubFetch((url) => (url === "/api/accounts/1/imports"
      ? json(422, { detail: { message: "2 rows are invalid", errors: [{ line: 3, message: "bad date" }, { line: 7, message: "bad amount" }] } })
      : undefined));
    renderPage();
    await screen.findAllByRole("option", { name: "Checking" });
    await userEvent.upload(screen.getByLabelText("CSV file"), new File(["x"], "bank.csv", { type: "text/csv" }));
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("2 rows are invalid");
    expect(within(alert).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Line 3: bad date", "Line 7: bad amount"]);
  });

  it("saves a category, offers to apply it to the merchant, and applies it", async () => {
    const patches: unknown[] = [];
    let applied = false;
    const fetchFn = stubFetch((url, init) => {
      if (url === "/api/transactions/1" && init.method === "PATCH") {
        const body = JSON.parse(init.body as string);
        patches.push(body);
        applied = body.apply_to_merchant;
        return json(200, {
          transaction: txn(1, { category: "Dining", category_source: "user" }),
          also_updated: applied ? 2 : 0, same_merchant_count: applied ? 0 : 2,
        });
      }
      if (url.startsWith("/api/transactions?")) {
        const cat = applied ? { category: "Dining", category_source: "correction" as const } : {};
        return json(200, page([txn(1, applied ? { category: "Dining" } : {}), txn(2, cat), txn(3, cat)]));
      }
      return undefined;
    });
    renderPage();
    const select = (await screen.findAllByRole("combobox", { name: /Category for Chipotle/ }))[0];
    await userEvent.selectOptions(select, "Dining");
    expect(await screen.findByText(/Also set 2 other Chipotle transactions to Dining/)).toBeInTheDocument();
    expect(patches[0]).toEqual({ category: "Dining", apply_to_merchant: false });

    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("Also set 2 other Chipotle transactions to Dining. Future imports will use it too."))
      .toBeInTheDocument();
    expect(patches[1]).toEqual({ category: "Dining", apply_to_merchant: true });
    await waitFor(() => expect(listCalls(fetchFn).length).toBeGreaterThan(1));
    expect(screen.queryByRole("button", { name: "Apply" })).not.toBeInTheDocument();
  });

  it("shows what a changed category was and can change it back", async () => {
    const patches: unknown[] = [];
    stubFetch((url, init) => {
      if (url === "/api/transactions/1" && init.method === "PATCH") {
        const body = JSON.parse(init.body as string);
        patches.push(body);
        return json(200, {
          transaction: txn(1, { category: body.category, category_source: "user" }), also_updated: 0, same_merchant_count: 0,
        });
      }
      if (url.startsWith("/api/transactions?")) return json(200, page([txn(1, { category: "Dining" })]));
      return undefined;
    });
    renderPage();
    const select = await screen.findByRole("combobox", { name: /Category for Chipotle/ });
    await userEvent.selectOptions(select, "Rent");
    expect(await screen.findByText(/Was Dining/)).toBeInTheDocument();
    expect(select).toHaveValue("Rent");

    await userEvent.click(screen.getByRole("button", { name: "Undo: change Chipotle back to Dining" }));
    await waitFor(() => expect(select).toHaveValue("Dining"));
    expect(patches).toEqual([{ category: "Rent", apply_to_merchant: false }, { category: "Dining", apply_to_merchant: false }]);
    expect(screen.queryByText(/Was Dining/)).not.toBeInTheDocument();
  });

  it("offers no undo when an uncategorized transaction gets its first category", async () => {
    stubFetch((url, init) => {
      if (url === "/api/transactions/1" && init.method === "PATCH") {
        return json(200, { transaction: txn(1, { category: "Dining", category_source: "user" }), also_updated: 0, same_merchant_count: 0 });
      }
      if (url.startsWith("/api/transactions?")) return json(200, page([txn(1)]));
      return undefined;
    });
    renderPage();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: /Category for Chipotle/ }), "Dining");
    await waitFor(() => expect(screen.getByRole("combobox", { name: /Category for Chipotle/ })).toHaveValue("Dining"));
    expect(screen.queryByText(/^Was /)).not.toBeInTheDocument();
  });

  it("keeps the old category and shows an error when saving fails", async () => {
    stubFetch((url, init) => {
      if (url === "/api/transactions/1" && init.method === "PATCH") return json(500, { detail: "boom" });
      if (url.startsWith("/api/transactions?")) return json(200, page([txn(1, { category: "Other" })]));
      return undefined;
    });
    renderPage();
    const select = await screen.findByRole("combobox", { name: /Category for Chipotle/ });
    await userEvent.selectOptions(select, "Dining");
    expect(await screen.findByText("Couldn't save the category. Try again.")).toBeInTheDocument();
    expect(select).toHaveValue("Other");
  });

  it("adds an account and selects it for the import", async () => {
    stubFetch((url, init) => (url === "/api/accounts" && init.method === "POST"
      ? json(201, { id: 2, name: "Discover", type: "credit" })
      : undefined));
    renderPage();
    await screen.findAllByRole("option", { name: "Checking" });
    await userEvent.click(screen.getByRole("button", { name: "Add another account" }));
    await userEvent.type(screen.getByLabelText("Account name"), "Discover");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "credit");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const importCard = screen.getByRole("region", { name: "Import transactions" });
    await waitFor(() => expect(within(importCard).getByLabelText("Account")).toHaveValue("2"));
  });
});
