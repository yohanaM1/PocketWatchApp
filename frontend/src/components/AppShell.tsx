import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { listFlags } from "../api/insights";
import { listTransactions } from "../api/transactions";
import { useAuth } from "../auth/AuthContext";
import { onFlagsChanged } from "../lib/flagEvents";
import { onReviewChanged } from "../lib/reviewEvents";
import { Logo } from "./Logo";

const icon = (path: ReactNode) => (
  <svg className="tab-icon" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false"
    fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    {path}
  </svg>
);

type Badge = "review" | "flags";

const TABS: { to: string; label: string; icon: ReactNode; badge?: Badge }[] = [
  { to: "/ask", label: "Ask", icon: icon(<path d="M4 5h16v11H9l-5 4z M8 9.5h8 M8 12.5h5" />) },
  {
    to: "/transactions", label: "Transactions",
    icon: icon(<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6 M9 12h6 M9 16h3" />),
  },
  {
    to: "/recurring", label: "Recurring",
    icon: icon(<path d="M20 11a8 8 0 0 0-14.3-4.9 M4 4v3.5h3.5 M4 13a8 8 0 0 0 14.3 4.9 M20 20v-3.5h-3.5" />),
  },
  {
    to: "/budgets", label: "Budgets",
    icon: icon(<path d="M3 7h18v12H3z M3 7l2-3h14l2 3 M16 13h2 M3 11h18" />),
  },
  {
    to: "/review", label: "Review", badge: "review",
    icon: icon(<path d="M4 6.5l1.8 1.8L9 5 M4 12.5l1.8 1.8L9 11 M4 18.5l1.8 1.8L9 17 M12 7h8 M12 13h8 M12 19h5" />),
  },
  {
    to: "/flags", label: "Flags", badge: "flags",
    icon: icon(<path d="M5 21V4 M5 4h11l-2 4 2 4H5" />),
  },
];

const BADGE_TEXT: Record<Badge, string> = { review: "need a category", flags: "open flags" };

/** The review-queue size and open-flag count; recounted on every route change and when either changes.
 * Imports and category edits send the review event, and they re-run flag detection too, so both recount on it. */
function useBadgeCounts(): Record<Badge, number> {
  const { pathname } = useLocation();
  const [counts, setCounts] = useState<Record<Badge, number>>({ review: 0, flags: 0 });
  useEffect(() => {
    let live = true;
    const set = (badge: Badge) => (n: unknown) => {
      if (live && typeof n === "number") setCounts((c) => ({ ...c, [badge]: n }));
    };
    // The badges are hints; never block the page on them.
    const refreshReview = () => {
      listTransactions({ review: true }, { limit: 1, offset: 0 }).then((p) => set("review")(p.total)).catch(() => {});
    };
    const refreshFlags = () => {
      listFlags("open").then((p) => set("flags")(p.open_count)).catch(() => {});
    };
    const refreshBoth = () => { refreshReview(); refreshFlags(); };
    refreshBoth();
    const offReview = onReviewChanged(refreshBoth);
    const offFlags = onFlagsChanged(refreshFlags);
    return () => { live = false; offReview(); offFlags(); };
  }, [pathname]);
  return counts;
}

/** Signed-in layout: navy sidebar on wide screens; a top header plus bottom tab bar on phones. */
export function AppShell() {
  const { logout } = useAuth();
  const counts = useBadgeCounts();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><Logo size={34} /><span>PocketWatch</span></div>
        <nav className="tabs" aria-label="Main">
          {TABS.map((t) => (
            <NavLink key={t.to} to={t.to} className="tab">
              {t.icon}
              <span>{t.label}</span>
              {t.badge && counts[t.badge] > 0 && (
                <>
                  <span className="tab-badge" aria-hidden="true">{counts[t.badge] > 99 ? "99+" : counts[t.badge]}</span>
                  <span className="sr-only">, {counts[t.badge]} {BADGE_TEXT[t.badge]}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
        <button type="button" className="logout" onClick={logout}>
          {icon(<path d="M15 4h4v16h-4 M10 8l-4 4 4 4 M6 12h10" />)}
          <span>Log out</span>
        </button>
      </aside>
      <div className="shell-main">
        <Outlet />
      </div>
    </div>
  );
}
