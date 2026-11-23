import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";

import { App } from "../App";

type Handler = (url: string, init: RequestInit) => Promise<Response>;

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function stubFetch(handler: Handler) {
  const fn = vi.fn((url: string, init: RequestInit = {}) => handler(url, init));
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Answers login/register as given; every page's data requests get an empty answer. */
function backend(auth: { login?: number; register?: number } = {}) {
  return stubFetch((url) => {
    if (url === "/api/auth/login") {
      return auth.login && auth.login !== 200 ? json(auth.login, { detail: "no" }) : json(200, { access_token: "t0k3n" });
    }
    if (url === "/api/auth/register") return json(auth.register ?? 201, { id: 1, email: "a@umd.edu" });
    if (url === "/api/accounts" || url === "/api/categories" || url.startsWith("/api/questions")) return json(200, []);
    return json(200, { items: [], total: 0, money_out: "0.00", money_in: "0.00", uncategorized_count: 0 });
  });
}

function Location() {
  const loc = useLocation();
  return <div data-testid="location">{loc.pathname + loc.search}</div>;
}

function renderAt(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <App />
      <Location />
    </MemoryRouter>,
  );
}

async function fill(email: string, password: string) {
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password"), password);
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginPage", () => {
  it("returns to the page you came from after logging in", async () => {
    backend();
    renderAt("/transactions?month=2026-09");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    await fill("a@umd.edu", "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("heading", { name: "Transactions" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/transactions?month=2026-09");
  });

  it("goes to /ask when opened directly", async () => {
    backend();
    renderAt("/login");
    await fill("a@umd.edu", "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("heading", { name: "Ask about your spending" })).toBeInTheDocument();
  });

  it("registers, then logs in", async () => {
    const fetchFn = backend();
    renderAt("/login");
    await userEvent.click(screen.getByRole("button", { name: "New here? Create an account" }));
    await fill("a@umd.edu", "correct-horse-1");
    await userEvent.type(screen.getByLabelText("Confirm password"), "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    await screen.findByRole("heading", { name: "Ask about your spending" });
    const urls = fetchFn.mock.calls.map(([url]) => url);
    expect(urls.indexOf("/api/auth/register")).toBeLessThan(urls.indexOf("/api/auth/login"));
  });

  it("blocks registration when the passwords don't match", async () => {
    const fetchFn = backend();
    renderAt("/login");
    await userEvent.click(screen.getByRole("button", { name: "New here? Create an account" }));
    await fill("a@umd.edu", "correct-horse-1");
    await userEvent.type(screen.getByLabelText("Confirm password"), "correct-horse-2");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    expect(screen.getByRole("alert")).toHaveTextContent("The passwords don't match.");
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it.each([
    [{ register: 409 }, "That email is already registered. Log in instead."],
    [{ register: 422 }, "Use a valid email and a password of at least eight characters."],
  ])("shows the register error for %o", async (auth, message) => {
    backend(auth);
    renderAt("/login");
    await userEvent.click(screen.getByRole("button", { name: "New here? Create an account" }));
    await fill("a@umd.edu", "correct-horse-1");
    await userEvent.type(screen.getByLabelText("Confirm password"), "correct-horse-1");
    await userEvent.click(screen.getByRole("button", { name: "Register" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });

  it("shows wrong-password and offline errors", async () => {
    backend({ login: 401 });
    renderAt("/login");
    await fill("a@umd.edu", "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong email or password.");

    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't reach PocketWatch. Is the backend running?");
  });

  it("switching modes hides the confirm field and clears the error", async () => {
    backend({ login: 401 });
    renderAt("/login");
    await fill("a@umd.edu", "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Log in" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "New here? Create an account" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Confirm password")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Have an account? Log in" }));
    expect(screen.queryByLabelText("Confirm password")).not.toBeInTheDocument();
  });
});
