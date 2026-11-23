import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import type { Flag, FlagStatus, TransactionOut } from "../api/types";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function txn(id: number, overrides: Partial<TransactionOut> = {}): TransactionOut {
  return {
    id, account_id: 1, account_name: "Checking", date: "2026-09-08", amount: "-25.14", merchant: "UBER",
    description: "UBER *TRIP HELP.UBER.COM CA", category: "Transport", category_source: "user", category_confidence: 1, in_review: false,
    ...overrides,
  };
}

const FLAGS: Flag[] = [
  { id: 3, kind: "unfamiliar_merchant", reason: "First charge from this merchant, and over $75", status: "open",
    created_at: "2026-11-14T12:00:00", transaction: txn(60, { merchant: "XJ4HOLDINGS", amount: "-225.56", date: "2026-10-24",
      description: "PAYPAL *XJ4HOLDINGS 4029357733", category: "Other" }) },
  { id: 1, kind: "duplicate", reason: "Same date, merchant and amount as another charge in this account", status: "open",
    created_at: "2026-11-14T12:00:00", transaction: txn(14) },
];

/** Flags by status, as the server would return them; PATCH moves a flag between statuses. */
function stubFetch(handler: Handler = () => undefined, flags: Flag[] = FLAGS) {
  const store = flags.map((f) => ({ ...f }));
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    if (url.startsWith("/api/anomalies?")) {
      const status = new URLSearchParams(url.split("?")[1]).get("status") as FlagStatus | "all";
      return json(200, {
        items: store.filter((f) => status === "all" || f.status === status),
        open_count: store.filter((f) => f.status === "open").length,
      });
    }
    const patch = url.match(/^\/api\/anomalies\/(\d+)$/);
    if (patch && init.method === "PATCH") {
      const flag = store.find((f) => f.id === Number(patch[1]))!;
      flag.status = JSON.parse(init.body as string).status;
      return json(200, flag);
    }
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
  return render(<MemoryRouter initialEntries={["/flags"]}><App /></MemoryRouter>);
}

const patches = (fn: ReturnType<typeof stubFetch>) =>
  fn.mock.calls.filter(([, init]) => init?.method === "PATCH").map(([url, init]) => [url, JSON.parse(init!.body as string)]);

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("FlagsPage", () => {
  it("lists open flags with their label, amount and reason", async () => {
    stubFetch();
    renderPage();
    const card = await screen.findByRole("article", { name: "XJ4HOLDINGS" });
    expect(within(card).getByText("New merchant")).toBeInTheDocument();
    expect(within(card).getByText("−$225.56")).toBeInTheDocument();
    expect(within(card).getByText("First charge from this merchant, and over $75")).toBeInTheDocument();
    expect(within(screen.getByRole("article", { name: "UBER" })).getByText("Possible duplicate")).toBeInTheDocument();
  });

  it("dismisses and confirms, and updates the nav badge", async () => {
    const fn = stubFetch();
    renderPage();
    await screen.findByRole("article", { name: "UBER" });
    await waitFor(() => expect(screen.getByRole("link", { name: /^Flags\s*,\s*2 open flags$/ })).toBeInTheDocument());

    await userEvent.click(within(screen.getByRole("article", { name: "UBER" })).getByRole("button", { name: "Looks fine" }));
    expect(await screen.findByText("Dismissed: marked as looking fine.")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("article", { name: "XJ4HOLDINGS" })).getByRole("button", { name: "Not right" }));
    expect(await screen.findByText("Confirmed: marked as not right.")).toBeInTheDocument();

    expect(patches(fn)).toEqual([["/api/anomalies/1", { status: "dismissed" }], ["/api/anomalies/3", { status: "confirmed" }]]);
    expect(await screen.findByRole("heading", { name: "All clear" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("link", { name: "Flags" })).toBeInTheDocument());
  });

  it("filters by status and reopens a dismissed flag", async () => {
    const fn = stubFetch(() => undefined, [{ ...FLAGS[1], status: "dismissed" }]);
    renderPage();
    expect(await screen.findByRole("heading", { name: "All clear" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Dismissed" }));
    const card = await screen.findByRole("article", { name: "UBER" });
    expect(screen.getByRole("button", { name: "Dismissed" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(card).getByRole("button", { name: "Reopen" }));
    expect(await screen.findByText("Reopened.")).toBeInTheDocument();
    expect(patches(fn)).toEqual([["/api/anomalies/1", { status: "open" }]]);
  });

  it("keeps the card and says so when saving fails", async () => {
    stubFetch((_url, init) => (init.method === "PATCH" ? json(500, {}) : undefined));
    renderPage();
    const card = await screen.findByRole("article", { name: "UBER" });
    await userEvent.click(within(card).getByRole("button", { name: "Looks fine" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("Couldn't save that. Try again.");
    expect(within(card).getByRole("button", { name: "Looks fine" })).toBeEnabled();
  });

  it("shows an error with retry", async () => {
    let fail = true;
    stubFetch((url) => (url.startsWith("/api/anomalies?") && fail ? json(500, {}) : undefined));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load your flags.");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("article", { name: "UBER" })).toBeInTheDocument();
  });
});
