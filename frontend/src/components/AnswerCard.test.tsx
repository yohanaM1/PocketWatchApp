import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AnswerCard } from "./AnswerCard";
import { answer } from "../test/fixtures";

const XSS = "<img src=x onerror=alert(1)>";

describe("AnswerCard", () => {
  it("recurring list: marked amounts and dates, no extra sentence, a recurring sources table", async () => {
    render(<AnswerCard answer={answer("recurring_fixture")} onRetry={() => {}} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    const total = screen.getByRole("button", { name: "$1,006.96, verified: Your active recurring payments, added up" });
    await userEvent.click(total);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Your active recurring payments, added up");
    await userEvent.click(screen.getByRole("button", { name: "$7.99, verified: NETFLIX · a month" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("NETFLIX · a month");
    expect(screen.queryByText("AI comment · not verified")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Show sources" }));
    const table = screen.getByRole("table", { name: "Recurring payments" });
    expect(table).toHaveTextContent("LANDMARK COLLEGE PARK RENT");
    expect(screen.getByText("Detected from your imported transactions through Oct 30, 2026.")).toBeInTheDocument();
  });

  it("flag list: merchant names stay plain text, with a flags sources table", async () => {
    const a = answer("flags_one");
    const result = a.final_results![0];
    if (!("kind" in (result.rows[0] ?? {}))) throw new Error("expected a flag result");
    render(<AnswerCard answer={a} onRetry={() => {}} />);
    expect(screen.getByRole("button", { name: "$25.14, verified: Possible duplicate · UBER" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show sources" }));
    expect(screen.getByRole("table", { name: "Open flags" })).toHaveTextContent("Possible duplicate");
  });

  it("verified: badge, caption, marked numbers, labeled extra, sources toggle", async () => {
    render(<AnswerCard answer={answer("single_dining_sep")} onRetry={() => {}} />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("Every number here was checked against your transactions.")).toBeInTheDocument();
    const num = screen.getByRole("button", { name: "$61.87, verified: Dining, September 2026" });
    expect(screen.getByText("AI comment · not verified")).toBeInTheDocument();
    expect(screen.getByText("Nice work keeping track.")).toBeInTheDocument();

    await userEvent.click(num);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Dining · Sep 1 – Sep 30, 2026 · 7 transactions");

    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show sources" }));
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("purchase count: marked count, Purchases column, transactions with merchant names as plain text", async () => {
    const a = answer("count_eat_out_oct");
    const result = a.final_results![0];
    if (!("count_row" in result)) throw new Error("expected a count result");
    result.rows = [{ row_id: "txn:1", date: "2026-10-03", amount: "-12.40", merchant: XSS, category: "Dining" }];
    render(<AnswerCard answer={a} onRetry={() => {}} />);
    const num = screen.getByRole("button", { name: "9, verified: Dining, October 2026" });
    await userEvent.click(num);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Dining · Oct 1 – Oct 31, 2026 · 9 purchases");

    await userEvent.click(screen.getByRole("button", { name: "Show sources" }));
    expect(screen.getByRole("columnheader", { name: "Purchases" })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Spending" })).not.toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Transactions" })).toHaveTextContent(XSS);
    expect(document.querySelector("img")).toBeNull();
  });

  it("fixed reply: text only, no badge", () => {
    render(<AnswerCard answer={answer("fixed_reply")} onRetry={() => {}} />);
    expect(screen.getByText(/can't answer that kind of question yet/)).toBeInTheDocument();
    expect(screen.queryByText("Verified")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("unverified: badge, source table open, never the extra sentence", () => {
    const a = { ...answer("unverified"), extra: "Should never show" };
    render(<AnswerCard answer={a} onRetry={() => {}} />);
    expect(screen.getByText("Couldn't verify")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("Not categorized yet (money out)")).toBeInTheDocument();
    expect(screen.queryByText("Should never show")).not.toBeInTheDocument();
  });

  it("failed: badge, apology and Try again", async () => {
    const onRetry = vi.fn();
    render(<AnswerCard answer={answer("failed")} onRetry={onRetry} />);
    expect(screen.getByText("No answer")).toBeInTheDocument();
    expect(screen.getByText(/couldn't answer that right now/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders injected markup in answer, extra and question as literal text", () => {
    const a = { ...answer("single_dining_sep"), extra: XSS, question: XSS };
    const { container } = render(<AnswerCard answer={a} onRetry={() => {}} />);
    expect(screen.getByText(XSS)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();

    const fixed = { ...answer("fixed_reply"), answer: XSS };
    const r2 = render(<AnswerCard answer={fixed} onRetry={() => {}} />);
    expect(r2.container.querySelector("img")).toBeNull();
  });
});
