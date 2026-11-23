import { isListPlan } from "../api/types";
import type { ListIntent, Plan, ToolResult } from "../api/types";
import { sourceRows, type Sources } from "../lib/sources";

/** The raw SQL rows behind an answer. Safe to show even when the written answer wasn't verified. */
export function SourcePanel({ plan, results }: { plan: Plan | null; results: ToolResult[] | null }) {
  const src = sourceRows(plan, results);
  if (!src) return null;
  if (isListPlan(plan)) return <ListSources intent={plan.intent} src={src} />;
  return (
    <section className="sources" aria-label="Sources">
      <p className="caption">Straight from your imported transactions.</p>
      {src.noData ? (
        <p>No imported transactions in this period.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">What</th><th scope="col">Period</th>
              {!src.countsPurchases && <th scope="col">Spending</th>}
              <th scope="col">{src.countsPurchases ? "Purchases" : "Transactions"}</th>
            </tr>
          </thead>
          <tbody>
            {src.rows.map((r) => (
              <tr key={r.rowId}>
                <td>{r.what}</td>
                <td>{r.period}</td>
                {!src.countsPurchases && (
                  <td className="amount">{r.spending}{r.refundsExceeded && " (refunds exceeded spending)"}</td>
                )}
                <td className="amount">{r.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {src.transactions.length > 0 && (
        <table aria-label="Transactions">
          <caption className="caption">
            {src.truncated ? `The first ${src.transactions.length} transactions` : "Transactions"}, refunds included
          </caption>
          <thead>
            <tr><th scope="col">Date</th><th scope="col">Merchant</th><th scope="col">Category</th><th scope="col">Amount</th></tr>
          </thead>
          <tbody>
            {src.transactions.map((t) => (
              <tr key={t.rowId}>
                <td>{t.date}</td>
                <td>{t.merchant}</td>
                <td>{t.category}</td>
                <td className="amount">{t.amount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** The active recurring payments or open flags behind a list answer, as stored when it was answered. */
function ListSources({ intent, src }: { intent: ListIntent; src: Sources }) {
  const recurring = intent === "recurring_list";
  return (
    <section className="sources" aria-label="Sources">
      <p className="caption">
        {recurring ? "Detected from your imported transactions" : "Flagged in your imported transactions"}
        {src.dataThrough && <> through {src.dataThrough}</>}.
      </p>
      {src.noData ? (
        <p>No imported transactions yet.</p>
      ) : recurring ? (
        <>
          {src.series.length > 0 && (
            <table aria-label="Recurring payments">
              <thead>
                <tr><th scope="col">Merchant</th><th scope="col" className="amount">Each month</th><th scope="col">Next expected</th></tr>
              </thead>
              <tbody>
                {src.series.map((r) => (
                  <tr key={r.rowId}><td>{r.merchant}</td><td className="amount">{r.amount}</td><td className="nowrap">{r.next}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          {src.stopped > 0 && <p className="caption">Stopped: {src.stopped}. See the Recurring page.</p>}
          {src.series.length === 0 && src.stopped === 0 && <p>No recurring payments found.</p>}
        </>
      ) : src.flags.length > 0 ? (
        <table aria-label="Open flags">
          <thead>
            <tr><th scope="col">Flag</th><th scope="col">Date</th><th scope="col">Merchant</th><th scope="col" className="amount">Amount</th></tr>
          </thead>
          <tbody>
            {src.flags.map((f) => (
              <tr key={f.rowId}><td>{f.label}</td><td className="nowrap">{f.date}</td><td>{f.merchant}</td><td className="amount">{f.amount}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>No open flags.</p>
      )}
    </section>
  );
}
