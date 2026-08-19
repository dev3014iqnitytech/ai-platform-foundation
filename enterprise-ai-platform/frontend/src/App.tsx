import { useIsAuthenticated, useMsal } from '@azure/msal-react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from '@/shared/layout/AppShell';
import { LoginPage } from '@/features/auth/LoginPage';
import { DashboardPage } from '@/features/dashboard/DashboardPage';
import { StorySearchPage } from '@/features/story-search/StorySearchPage';
import { TestCasesPage } from '@/features/test-cases/TestCasesPage';
import { ApprovalQueuePage } from '@/features/approval-queue/ApprovalQueuePage';
import { ReviewPage } from '@/features/review/ReviewPage';
import { KnowledgeBasePage } from '@/features/knowledge-base/KnowledgeBasePage';
import { AuditLogsPage } from '@/features/audit-logs/AuditLogsPage';
import { AdminPage } from '@/features/admin/AdminPage';
import { LOCAL_TOKEN_KEY } from '@/services/api.client';

const LOCAL_MODE = import.meta.env.VITE_LOCAL_MODE === 'true';

function useIsLoggedIn(): boolean {
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const msalAuth = useIsAuthenticated();
  if (LOCAL_MODE) return !!localStorage.getItem(LOCAL_TOKEN_KEY);
  return msalAuth;
}

function AuthGuard({ children }: { children: React.ReactNode }) {
  const loggedIn = useIsLoggedIn();
  if (!loggedIn) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const loggedIn = useIsLoggedIn();

  return (
    <Routes>
      <Route
        path="/login"
        element={loggedIn ? <Navigate to="/" replace /> : <LoginPage />}
      />
      <Route
        path="/"
        element={
          <AuthGuard>
            <AppShell />
          </AuthGuard>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="stories" element={<StorySearchPage />} />
        <Route path="test-cases/:sessionId?" element={<TestCasesPage />} />
        <Route path="approvals" element={<ApprovalQueuePage />} />
        <Route path="review/:sessionId" element={<ReviewPage />} />
        <Route path="knowledge" element={<KnowledgeBasePage />} />
        <Route path="audit" element={<AuditLogsPage />} />
        <Route path="admin" element={<AdminPage />} />
      </Route>
    </Routes>
  );
}
