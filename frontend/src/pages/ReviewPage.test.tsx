import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import type { ReviewGroup, TransactionOut } from "../api/types";
import { reasonText } from "./ReviewPage";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const CATEGORIES = ["Dining", "Groceries", "Shopping", "Other"];

function txn(id: number, overrides: Partial<TransactionOut> = {}): TransactionOut {
  return {
    id, account_id: 1, account_name: "Checking", date: "2026-10-05", amount: "-6.45", merchant: "STARBUCKS",
    description: "SQ *STARBUCKS #2411 HYATTSVILLE MD", category: null, category_source: null, category_confidence: null,
    in_review: true, // queued rows
    ...overrides,
  };
}

function group(merchant: string, overrides: Partial<ReviewGroup> = {}): ReviewGroup {
  return {
    merchant, count: 3, reason: "uncategorized", guesses: [], suggestion: null, mixed: false, mixed_reason: null,
    money_out: "15.80", money_in: "0.00", first_date: "2026-09-03", last_date: "2026-10-01", examples: [txn(1)],
    ...overrides,
  };
}

const reviewPage = (groups: ReviewGroup[], transactions = groups.reduce((n, g) => n + g.count, 0)) =>
  ({ groups, total_groups: groups.length, total_transactions: transactions });

/** Stub fetch: `handler` answers first; categories, an empty queue and an empty transaction list are the defaults. */
function stubFetch(handler: Handler = () => undefined) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    if (url === "/api/categories") return json(200, CATEGORIES);
    if (url === "/api/corrections") return json(200, []); // the Saved rules section
    if (url.startsWith("/api/review?")) return json(200, reviewPage([]));
    if (url.startsWith("/api/transactions?")) {
      return json(200, { items: [], total: 0, money_out: "0.00", money_in: "0.00", uncategorized_count: 0 });
    }
    return json(404, { detail: "unexpected" });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderPage() {
  sessionStorage.setItem("pocketwatch.token", "t0k3n");
  return render(<MemoryRouter initialEntries={["/review"]}><App /></MemoryRouter>);
}

const resolveBodies = (fn: ReturnType<typeof stubFetch>) =>
  fn.mock.calls.filter(([url]) => url === "/api/review/resolve").map(([, init]) => JSON.parse(init!.body as string));

const card = (name: string) => screen.getByRole("article", { name });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reasonText", () => {
  it("names no guess, one guess and disagreeing guesses", () => {
    expect(reasonText(group("A"))).toBe("No category");
    expect(reasonText(group("A", { guesses: [{ category: "Dining", count: 3, confidence: 0.6 }] })))
      .toBe("Guess: Dining · 60% sure");
    expect(reasonText(group("A", { guesses: [{ category: "Dining", count: 3, confidence: null }] }))).toBe("Guess: Dining");
    expect(reasonText(group("A", {
      guesses: [{ category: "Groceries", count: 5, confidence: 0.6 }, { category: "Shopping", count: 2, confidence: 0.4 }],
    }))).toBe("AI guesses differ: Groceries ×5, Shopping ×2");
  });
});

describe("ReviewPage", () => {
  it("reloads the queue when a removed rule sends its transactions back to review", async () => {
    const fetch = stubFetch((url, init) => {
      if (url === "/api/corrections") {
        return json(200, [{ id: 4, merchant: "STARBUCKS", category: "Dining", transaction_count: 3, mixed: false }]);
      }
      if (init.method === "DELETE") return json(200, { sent_to_review: 3 });
      return undefined;
    });
    renderPage();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Remove the STARBUCKS rule" }));
    await user.click(screen.getByRole("radio", { name: "Send its 3 transactions back to review" }));
    const before = fetch.mock.calls.filter(([url]) => url.startsWith("/api/review?")).length;
    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.startsWith("/api/review?")).length)
      .toBeGreaterThan(before));
  });

  it("lists merchant groups with their summary and reason", async () => {
    stubFetch((url) => (url.startsWith("/api/review?")
      ? json(200, reviewPage([
        group("STARBUCKS"),
        group("GIANT", { count: 1, guesses: [{ category: "Groceries", count: 1, confidence: 0.55 }], suggestion: "Groceries",
          reason: "low_confidence", money_out: "47.73", first_date: "2026-10-05", last_date: "2026-10-05" }),
      ]))
      : undefined));
    renderPage();
    expect(await screen.findByText(/need a category/)).toHaveTextContent("4 transactions from 2 merchants need a category.");
    const sb = card("STARBUCKS");
    expect(sb).toHaveTextContent("3 transactions · Sep 3, 2026 – Oct 1, 2026 · $15.80 out");
    expect(within(sb).getByText("No category")).toBeInTheDocument();
    const giant = card("GIANT");
    expect(within(giant).getByText("Guess: Groceries · 55% sure")).toBeInTheDocument();
    expect(within(giant).getByRole("combobox", { name: "Category" })).toHaveValue("Groceries");
    expect(within(giant).getByRole("checkbox", { name: "Remember for future imports" })).toBeChecked();
  });

  it("Apply needs a category, sends remember by default and replaces the card with a confirmation", async () => {
    const fetchFn = stubFetch((url) => {
      if (url.startsWith("/api/review?")) return json(200, reviewPage([group("STARBUCKS")]));
      if (url === "/api/review/resolve") return json(200, { updated: 3, also_updated: 1 });
      return undefined;
    });
    renderPage();
    const apply = await screen.findByRole("button", { name: "Apply to all 3" });
    expect(apply).toBeDisabled();
    await userEvent.selectOptions(within(card("STARBUCKS")).getByRole("combobox", { name: "Category" }), "Dining");
    await userEvent.click(apply);
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Set 3 STARBUCKS transactions to Dining · also updated 1 · remembered for future imports.");
    expect(resolveBodies(fetchFn)).toEqual([{ merchant: "STARBUCKS", category: "Dining", remember: true }]);
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "All caught up" })).toBeInTheDocument();
  });

  it("unchecking Remember sends remember: false and says nothing about future imports", async () => {
    const fetchFn = stubFetch((url) => {
      if (url.startsWith("/api/review?")) return json(200, reviewPage([group("GIANT", { suggestion: "Groceries" })]));
      if (url === "/api/review/resolve") return json(200, { updated: 3, also_updated: 0 });
      return undefined;
    });
    renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Remember for future imports" }));
    await userEvent.click(screen.getByRole("button", { name: "Apply to all 3" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Set 3 GIANT transactions to Groceries.");
    expect(resolveBodies(fetchFn)[0].remember).toBe(false);
  });

  it("keeps the card and shows an error when the resolve fails", async () => {
    stubFetch((url) => {
      if (url.startsWith("/api/review?")) return json(200, reviewPage([group("GIANT", { suggestion: "Groceries" })]));
      if (url === "/api/review/resolve") return json(500, { detail: "boom" });
      return undefined;
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Apply to all 3" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't save the category. Try again.");
    expect(card("GIANT")).toBeInTheDocument();
  });

  it("a mixed merchant is expanded, labeled, and doesn't remember by default", async () => {
    const fetchFn = stubFetch((url) => {
      if (url.startsWith("/api/review?")) {
        return json(200, reviewPage([group("AMAZON", { count: 2, mixed: true, mixed_reason: "known_mixed" })]));
      }
      if (url.includes("merchant=AMAZON")) {
        return json(200, { items: [txn(7, { merchant: "AMAZON", description: "AMZN Mktp US*2K4L1", date: "2026-10-02" }),
          txn(8, { merchant: "AMAZON", description: "AMAZON.COM*RT3", date: "2026-09-12" })], total: 2,
          money_out: "0.00", money_in: "0.00", uncategorized_count: 2 });
      }
      if (url === "/api/transactions/7") {
        return json(200, { transaction: txn(7, { category: "Shopping" }), also_updated: 0, same_merchant_count: 1 });
      }
      return undefined;
    });
    renderPage();
    const amazon = await screen.findByRole("article", { name: "AMAZON" });
    expect(amazon).toHaveTextContent("Mixed merchant: usually needs a category per purchase.");
    expect(within(amazon).getByRole("checkbox", { name: "Remember for future imports" })).not.toBeChecked();

    const row = await within(amazon).findByRole("combobox", { name: "Category for AMAZON on Oct 2, 2026" });
    expect(fetchFn.mock.calls.some(([url]) => (url as string).includes("merchant=AMAZON&review=true"))).toBe(true);
    await userEvent.selectOptions(row, "Shopping");
    await waitFor(() => expect(within(amazon).queryByRole("combobox", { name: "Category for AMAZON on Oct 2, 2026" }))
      .not.toBeInTheDocument());
    const patch = fetchFn.mock.calls.find(([url]) => url === "/api/transactions/7")!;
    expect(JSON.parse(patch[1]!.body as string)).toEqual({ category: "Shopping", apply_to_merchant: false });
    expect(within(amazon).getByRole("button", { name: "Apply to all 1" })).toBeInTheDocument();
  });

  it("Show transactions expands a normal group on demand", async () => {
    const fetchFn = stubFetch((url) => (url.startsWith("/api/review?") ? json(200, reviewPage([group("STARBUCKS")])) : undefined));
    renderPage();
    await screen.findByRole("article", { name: "STARBUCKS" });
    expect(fetchFn.mock.calls.some(([url]) => (url as string).includes("merchant="))).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Show transactions" }));
    await waitFor(() => expect(fetchFn.mock.calls.some(([url]) => (url as string).includes("merchant=STARBUCKS"))).toBe(true));
    expect(screen.getByRole("button", { name: "Hide transactions" })).toBeInTheDocument();
  });

  it("says all caught up, pointing to import when there are no transactions at all", async () => {
    stubFetch();
    renderPage();
    expect(await screen.findByRole("heading", { name: "All caught up" })).toBeInTheDocument();
    expect(await screen.findByText(/No transactions yet/)).toBeInTheDocument();
  });

  it("shows a retry when the queue can't load", async () => {
    let calls = 0;
    stubFetch((url) => {
      if (!url.startsWith("/api/review?")) return undefined;
      calls += 1;
      return calls === 1 ? json(500, { detail: "boom" }) : json(200, reviewPage([group("STARBUCKS")]));
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("article", { name: "STARBUCKS" })).toBeInTheDocument();
  });
});

describe("Review badge", () => {
  it("shows the queue size in the nav and hides at zero", async () => {
    let queued = 12;
    stubFetch((url) => (url.startsWith("/api/transactions?") && url.includes("review=true")
      ? json(200, { items: [], total: queued, money_out: "0.00", money_in: "0.00", uncategorized_count: 0 })
      : undefined));
    const { unmount } = renderPage();
    expect(await screen.findByRole("link", { name: /^Review\s*,\s*12 need a category$/ })).toBeInTheDocument();
    unmount();
    queued = 0;
    renderPage();
    await waitFor(() => expect(screen.getByRole("link", { name: "Review" })).toBeInTheDocument());
  });
});
