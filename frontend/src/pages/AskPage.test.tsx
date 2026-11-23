import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "../App";
import { ASK_TIMEOUT_MS } from "./AskPage";
import { answer } from "../test/fixtures";

type Handler = (url: string, init: RequestInit) => Promise<Response>;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function stubFetch(handler: Handler) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => handler(url, init));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderApp() {
  sessionStorage.setItem("pocketwatch.token", "t0k3n");
  return render(
    <MemoryRouter initialEntries={["/ask"]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("AskPage", () => {
  it("asks, shows the verified answer, and prepends it to history", async () => {
    const verified = answer("single_dining_sep");
    const fetchFn = stubFetch((_url, init) => {
      if (init.method === "POST") return json(201, verified);
      return json(200, []);
    });
    renderApp();
    await userEvent.type(screen.getByRole("textbox"), "How much did I spend on dining in September?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText("Every number here was checked against your transactions.")).toBeInTheDocument();
    const post = fetchFn.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(post[0]).toBe("/api/questions");
    expect((post[1]!.headers as Record<string, string>).Authorization).toBe("Bearer t0k3n");
    expect(screen.getByRole("region", { name: "History" })).toHaveTextContent(verified.question);
  });

  it("example questions fill the box", async () => {
    stubFetch(() => json(200, []));
    renderApp();
    await userEvent.click(screen.getByRole("button", { name: "“What did I spend on food last month?”" }));
    expect(screen.getByRole("textbox")).toHaveValue("What did I spend on food last month?");
  });

  it("recovers a lost answer from history after the 30 s timeout", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const stored = { ...answer("single_dining_sep"), id: 99, question: "How much did I spend on dining in September?" };
    let historyCalls = 0;
    stubFetch((url, init) => {
      if (init.method === "POST") {
        return new Promise((_, reject) => {
          init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        });
      }
      if (!url.startsWith("/api/questions")) return json(200, { items: [], total: 0 }); // the nav's review badge
      historyCalls += 1;
      return json(200, historyCalls === 1 ? [] : [stored]);
    });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderApp();
    await waitFor(() => expect(historyCalls).toBe(1));
    await user.type(screen.getByRole("textbox"), stored.question);
    await user.click(screen.getByRole("button", { name: "Ask" }));
    expect(screen.getByRole("status")).toHaveTextContent("this can take up to 20 seconds");
    expect(screen.getByRole("textbox")).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ASK_TIMEOUT_MS);
    });
    expect(await screen.findByText("Every number here was checked against your transactions.")).toBeInTheDocument();
    expect(historyCalls).toBe(2);
  });

  it("shows the lost-connection message when history has no match", async () => {
    stubFetch((_url, init) => (init.method === "POST" ? Promise.reject(new TypeError("network")) : json(200, [])));
    renderApp();
    await userEvent.type(screen.getByRole("textbox"), "dining in September?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/We lost the connection/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh history" })).toBeInTheDocument();
  });

  it("a 401 logs out and goes to /login", async () => {
    stubFetch(() => json(401, { detail: "Invalid or expired token" }));
    renderApp();
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(sessionStorage.getItem("pocketwatch.token")).toBeNull();
  });

  it("a 429 shows the rate-limit message and keeps the box filled", async () => {
    stubFetch((_url, init) => (init.method === "POST" ? json(429, { detail: "Too many" }) : json(200, [])));
    renderApp();
    await userEvent.type(screen.getByRole("textbox"), "dining in September?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect(await screen.findByText(/asked a lot of questions in the last minute/)).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("dining in September?");
  });

  it("failed answers retry the same question", async () => {
    const posts: string[] = [];
    stubFetch((_url, init) => {
      if (init.method === "POST") {
        const question = JSON.parse(init.body as string).question;
        posts.push(question);
        return json(201, { ...answer("failed"), id: posts.length, question });
      }
      return json(200, []);
    });
    renderApp();
    await userEvent.type(screen.getByRole("textbox"), "dining in September?");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    await userEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await waitFor(() => expect(posts).toEqual(["dining in September?", "dining in September?"]));
  });

  it("history shows older pages with before_id and never re-asks", async () => {
    const page = Array.from({ length: 20 }, (_, i) => ({ ...answer("fixed_reply"), id: 40 - i, question: `q${40 - i}` }));
    const fetchFn = stubFetch((url) =>
      json(200, url.includes("before_id=21") ? [{ ...answer("fixed_reply"), id: 5, question: "q5" }] : page));
    renderApp();
    await userEvent.click(await screen.findByRole("button", { name: "Show older" }));
    expect(await screen.findByText("q5")).toBeInTheDocument();
    expect(fetchFn.mock.calls.every(([, init]) => (init?.method ?? "GET") === "GET")).toBe(true);
  });
});
