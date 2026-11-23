import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { AskPage } from "./pages/AskPage";
import { BudgetsPage } from "./pages/BudgetsPage";
import { FlagsPage } from "./pages/FlagsPage";
import { LoginPage } from "./pages/LoginPage";
import { RecurringPage } from "./pages/RecurringPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TransactionsPage } from "./pages/TransactionsPage";

/** Where LoginPage sends the user back to after logging in. */
export interface LoginState {
  from?: { pathname: string; search: string };
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { token } = useAuth();
  const location = useLocation();
  const state: LoginState = { from: { pathname: location.pathname, search: location.search } };
  return token ? <>{children}</> : <Navigate to="/login" replace state={state} />;
}

/** Routes; the caller provides the router (BrowserRouter in main.tsx, MemoryRouter in tests). */
export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route path="/ask" element={<AskPage />} />
          <Route path="/transactions" element={<TransactionsPage />} />
          <Route path="/recurring" element={<RecurringPage />} />
          <Route path="/budgets" element={<BudgetsPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/flags" element={<FlagsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/ask" replace />} />
      </Routes>
    </AuthProvider>
  );
}
