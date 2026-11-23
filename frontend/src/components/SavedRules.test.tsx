import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { SavedRule } from "../api/types";
import { notifyReviewChanged } from "../lib/reviewEvents";
import { SavedRules } from "./SavedRules";

type Handler = (url: string, init: RequestInit) => Promise<Response> | undefined;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const CATEGORIES = ["Dining", "Groceries", "Shopping", "Education", "Transfers"];

const rule = (id: number, merchant: string, category: string, count: number, mixed = false): SavedRule =>
  ({ id, merchant, category, transaction_count: count, mixed });

const RULES = [rule(1, "AMAZON", "Shopping", 12, true), rule(2, "STARBUCKS", "Dining", 9)];

/** A fake API that keeps its rules, so a reload after a change sees it (as the real server would). */
function stubFetch(handler: Handler = () => undefined) {
  let server = RULES.map((r) => ({ ...r }));
  const fn = vi.fn((url: string, init: RequestInit = {}) => {
    const custom = handler(url, init);
    if (custom) return custom;
    const method = init.method ?? "GET";
    if (url === "/api/corrections" && method === "GET") return json(200, server);
    const id = Number(url.match(/^\/api\/corrections\/(\d+)/)?.[1]);
    const found = server.find((r) => r.id === id);
    if (found && method === "PATCH") {
      found.category = JSON.parse(String(init.body)).category;
      return json(200, { rule: found, updated: found.transaction_count });
    }
    if (found && method === "DELETE") {
      server = server.filter((r) => r !== found);
      return json(200, { sent_to_review: url.endsWith("send_to_review=true") ? found.transaction_count : 0 });
    }
    return json(404, { detail: "unexpected" });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const calls = (fn: ReturnType<typeof stubFetch>, method: string) =>
  fn.mock.calls.filter(([, init]) => (init?.method ?? "GET") === method).map(([url, init]) => [url, init?.body]);

const ruleRow = (merchant: string) =>
  screen.getByText(merchant).closest("li") as HTMLElement;

function renderRules(onSentToReview = vi.fn()) {
  render(<SavedRules categories={CATEGORIES} onSentToReview={onSentToReview} />);
  return onSentToReview;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SavedRules", () => {
  it("lists rules with their category, count and the mixed-merchant label", async () => {
    stubFetch();
    renderRules();

    expect(await screen.findByRole("heading", { name: "Saved rules (2)" })).toBeInTheDocument();
    const amazon = within(ruleRow("AMAZON"));
    expect(amazon.getByRole("combobox", { name: "Category for AMAZON" })).toHaveValue("Shopping");
    expect(amazon.getByText("12 transactions")).toBeInTheDocument();
    expect(amazon.getByText("Mixed merchant")).toBeInTheDocument();
    expect(within(ruleRow("STARBUCKS")).queryByText("Mixed merchant")).not.toBeInTheDocument();
  });

  it("changes a rule's category and says how many transactions moved", async () => {
    const fetch = stubFetch();
    renderRules();
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByRole("combobox", { name: "Category for STARBUCKS" }), "Groceries");
    const confirm = screen.getByRole("group", { name: "Change the STARBUCKS rule?" });
    expect(confirm).toHaveTextContent("Change the STARBUCKS rule from Dining to Groceries?");
    expect(confirm).toHaveTextContent("This moves its 9 transactions from Dining to Groceries");
    expect(calls(fetch, "PATCH")).toEqual([]); // nothing saved until Change
    await user.click(within(confirm).getByRole("button", { name: "Change" }));
    await waitFor(() => expect(calls(fetch, "PATCH")).toEqual([["/api/corrections/2", '{"category":"Groceries"}']]));
    expect(await within(ruleRow("STARBUCKS")).findByText("Updated 9 transactions")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Category for STARBUCKS" })).toHaveValue("Groceries");
  });

  it("puts the old category back when a change is cancelled", async () => {
    const fetch = stubFetch();
    renderRules();
    const user = userEvent.setup();

    const select = await screen.findByRole("combobox", { name: "Category for AMAZON" });
    await user.selectOptions(select, "Education");
    expect(select).toHaveValue("Education");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(select).toHaveValue("Shopping");
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(calls(fetch, "PATCH")).toEqual([]);
  });

  it("removes a rule and keeps its transactions by default", async () => {
    const fetch = stubFetch();
    const onSentToReview = renderRules();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Remove the STARBUCKS rule" }));
    const confirm = screen.getByRole("group", { name: "Remove the STARBUCKS → Dining rule?" });
    expect(within(confirm).getByRole("radio", { name: "Keep its 9 transactions as they are" })).toBeChecked();
    await user.click(within(confirm).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(calls(fetch, "DELETE")).toEqual([["/api/corrections/2?send_to_review=false", undefined]]));
    expect(await screen.findByText("Removed the STARBUCKS rule. Its transactions keep their category.")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Category for STARBUCKS" })).not.toBeInTheDocument();
    expect(onSentToReview).not.toHaveBeenCalled();
  });

  it("can send a removed rule's transactions back to review", async () => {
    const fetch = stubFetch();
    const onSentToReview = renderRules();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Remove the AMAZON rule" }));
    await user.click(screen.getByRole("radio", { name: "Send its 12 transactions back to review" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(calls(fetch, "DELETE")).toEqual([["/api/corrections/1?send_to_review=true", undefined]]));
    expect(await screen.findByText("Removed the AMAZON rule · 12 transactions sent back to review.")).toBeInTheDocument();
    expect(onSentToReview).toHaveBeenCalledTimes(1);
  });

  it("cancels a removal", async () => {
    const fetch = stubFetch();
    renderRules();
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Remove the AMAZON rule" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group")).not.toBeInTheDocument();
    expect(calls(fetch, "DELETE")).toEqual([]);
  });

  it("shows the empty state and picks up a rule saved while reviewing", async () => {
    let list: SavedRule[] = [];
    stubFetch((url) => (url === "/api/corrections" ? json(200, list) : undefined));
    renderRules();

    expect(await screen.findByText(/No saved rules yet/)).toBeInTheDocument();
    list = [rule(3, "GIANT", "Groceries", 1)];
    notifyReviewChanged(); // what resolving a merchant with Remember on sends
    expect(await screen.findByText("GIANT")).toBeInTheDocument();
  });

  it("says when rules can't load and retries", async () => {
    let fail = true;
    stubFetch((url) => (url === "/api/corrections" && fail ? json(500, { detail: "boom" }) : undefined));
    renderRules();
    const user = userEvent.setup();

    expect(await screen.findByText("Couldn't load your saved rules.")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("AMAZON")).toBeInTheDocument();
  });
});
