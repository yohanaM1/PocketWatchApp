// Each category's jewel color (a CSS variable defined in styles.css). Spending categories get distinct
// jewels where possible; Income shares emerald with money in, and Transfers/Other are neutral slate.
const JEWELS: Record<string, string> = {
  Dining: "ruby",
  Groceries: "emerald",
  Rent: "sapphire",
  Utilities: "teal",
  Transport: "topaz",
  Subscriptions: "amethyst",
  Shopping: "rose",
  Entertainment: "amethyst",
  Education: "sapphire",
  Health: "teal",
  Income: "emerald",
  Transfers: "slate",
  Other: "slate",
};

/** The CSS color for a category's chip; uncategorized (or unknown) is slate. */
export function jewel(category: string | null): string {
  return `var(--jewel-${(category && JEWELS[category]) || "slate"})`;
}
