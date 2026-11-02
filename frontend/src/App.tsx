import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Role } from "./api/types";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { useAuth } from "./lib/auth";
import { AdminPage } from "./pages/AdminPage";
import { AnalysisPage } from "./pages/AnalysisPage";
import { AskPage } from "./pages/AskPage";
import { DocumentPage } from "./pages/DocumentPage";
import { InsightsPage } from "./pages/InsightsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { SearchPage } from "./pages/SearchPage";
import { WorkspacesPage } from "./pages/WorkspacesPage";

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { can } = useAuth();
  return can(role) ? <>{children}</> : <p className="text-sm text-slate-600">You don't have access to this page.</p>;
}

export function App() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  if (!user) return <Routes><Route path="*" element={<LoginPage />} /></Routes>;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<LibraryPage />} />
        <Route path="documents/:id" element={<DocumentPage />} />
        <Route path="ask" element={<AskPage />} />
        <Route path="ask/:conversationId" element={<AskPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="analysis" element={<AnalysisPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="workspaces" element={<WorkspacesPage />} />
        <Route path="admin" element={<RequireRole role="admin"><AdminPage /></RequireRole>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
