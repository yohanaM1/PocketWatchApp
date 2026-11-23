import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import type { RecurringPage, RecurringSeries, TransactionOut } from "../api/types";
import { nextText } from "./RecurringPage";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function series(merchant: string, overrides: Partial<RecurringSeries> = {}): RecurringSeries {
  return {
    key: `${merchant}:2026-09-23:7.99`, merchant, category: "Subscriptions", amount: "-7.99", previous_amount: null,
    amount_changed_on: null, interval_days: 30, first_date: "2026-09-23", last_date: "2026-10-21",
    next_expected: "2099-11-20", charge_count: 2, active: true, ...overrides,
  };
}

function page(list: RecurringSeries[], overrides: Partial<RecurringPage> = {}): RecurringPage {
  const active = list.filter((s) => s.active);
  const total = active.reduce((n, s) => n + Number(s.amount), 0).toFixed(2);
  return { as_of: "2026-10-30", monthly_total: total, active_count: active.length, series: list, ...overrides };
}

function txn(id: number, date: string): TransactionOut {
  return {
    id, account_id: 1, account_name: "Checking", date, amount: "-7.99", merchant: "NETFLIX",
    description: "NETFLIX.COM 8821 CA", category: "Subscriptions", category_source: "user", category_confidence: 1, in_review: false,
  };
}

function stubFetch(handler: Handler = () => undefined) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    if (url === "/api/recurring") return json(200, page([]));
    if (url.startsWith("/api/anomalies?")) return json(200, { items: [], open_count: 0 });
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
  return render(<MemoryRouter initialEntries={["/recurring"]}><App /></MemoryRouter>);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("nextText", () => {
  it("says when a next charge is past and not imported yet", () => {
    expect(nextText("2026-11-18", "2026-11-14")).toBe("next Nov 18, 2026");
    expect(nextText("2026-10-21", "2026-11-14")).toBe("expected Oct 21, 2026 (not imported yet)");
  });
});

describe("RecurringPage", () => {
  it("shows the monthly total, the data date and each active payment", async () => {
    stubFetch((url) => (url === "/api/recurring"
      ? json(200, page([series("LANDMARK COLLEGE PARK RENT", { amount: "-925.00", category: "Rent" }), series("NETFLIX")]))
      : undefined));
    renderPage();

    expect(await screen.findByText("$932.99")).toBeInTheDocument();
    expect(screen.getByText("across 2 active payments")).toBeInTheDocument();
    expect(screen.getByText("Based on transactions through Oct 30, 2026.")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "Active recurring payments" });
    const rows = within(list).getAllByRole("listitem");
    expect(within(rows[0]).getByText("LANDMARK COLLEGE PARK RENT")).toBeInTheDocument();
    expect(within(rows[0]).getByText("$925.00")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/every ~30 days · last charged Oct 21, 2026 · next Nov 20, 2099/)).toBeInTheDocument();
  });

  it("shows a price change", async () => {
    stubFetch((url) => (url === "/api/recurring"
      ? json(200, page([series("NETFLIX", { amount: "-8.99", previous_amount: "-7.99", amount_changed_on: "2026-07-20" })]))
      : undefined));
    renderPage();
    expect(await screen.findByText(/was \$7\.99 until Jul 20, 2026/)).toBeInTheDocument();
  });

  it("keeps stopped payments collapsed until asked", async () => {
    stubFetch((url) => (url === "/api/recurring"
      ? json(200, page([series("NETFLIX"), series("PLANET FITNESS CLUB", { active: false, next_expected: null, amount: "-10.00" })]))
      : undefined));
    renderPage();
    const toggle = await screen.findByRole("button", { name: "Show 1 stopped payment" });
    expect(screen.queryByText("PLANET FITNESS CLUB")).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(screen.getByText("PLANET FITNESS CLUB")).toBeInTheDocument();
    expect(screen.getByText("across 1 active payment")).toBeInTheDocument();
  });

  it("loads a payment's charges by key", async () => {
    const fn = stubFetch((url) => {
      if (url === "/api/recurring") return json(200, page([series("NETFLIX")]));
      if (url.startsWith("/api/recurring/transactions?")) return json(200, [txn(2, "2026-10-21"), txn(1, "2026-09-23")]);
      return undefined;
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Show 2 charges" }));
    const charges = await screen.findByRole("list", { name: "NETFLIX charges" });
    expect(within(charges).getAllByRole("listitem")).toHaveLength(2);
    expect(fn.mock.calls.map(([u]) => u)).toContain(
      `/api/recurring/transactions?${new URLSearchParams({ key: "NETFLIX:2026-09-23:7.99" }).toString()}`);
  });

  it("explains a key that's gone and offers a refresh", async () => {
    let calls = 0;
    stubFetch((url) => {
      if (url === "/api/recurring") { calls += 1; return json(200, page([series("NETFLIX")])); }
      if (url.startsWith("/api/recurring/transactions?")) return json(404, { detail: "Recurring payment not found" });
      return undefined;
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Show 2 charges" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This list changed. Refresh to see the latest.");
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(calls).toBe(2);
  });

  it("has an empty state", async () => {
    stubFetch((url) => (url === "/api/recurring" ? json(200, page([], { as_of: null })) : undefined));
    renderPage();
    expect(await screen.findByRole("heading", { name: "No recurring payments yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import a CSV" })).toHaveAttribute("href", "/transactions");
  });

  it("shows an error with retry", async () => {
    let fail = true;
    stubFetch((url) => (url === "/api/recurring" ? (fail ? json(500, {}) : json(200, page([series("NETFLIX")]))) : undefined));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your recurring payments.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("NETFLIX")).toBeInTheDocument();
  });
});
