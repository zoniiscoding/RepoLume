import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../auth/AuthProvider";
import { RequireAuth } from "../auth/RequireAuth";
import { LoadingPage } from "../components/LoadingPage";

const SignInPage = lazy(async () => ({
  default: (await import("../features/auth/SignInPage")).SignInPage,
}));
const OAuthCallbackPage = lazy(async () => ({
  default: (await import("../features/auth/OAuthCallbackPage")).OAuthCallbackPage,
}));
const AppShell = lazy(async () => ({ default: (await import("../layouts/AppShell")).AppShell }));
const RepositoryListPage = lazy(async () => ({
  default: (await import("../features/repositories/RepositoryListPage")).RepositoryListPage,
}));
const RepositoryOverviewPage = lazy(async () => ({
  default: (await import("../features/repositories/RepositoryOverviewPage")).RepositoryOverviewPage,
}));
const QuestionWorkspacePage = lazy(async () => ({
  default: (await import("../features/questions/QuestionWorkspacePage")).QuestionWorkspacePage,
}));
const SettingsPage = lazy(async () => ({
  default: (await import("../features/settings/SettingsPage")).SettingsPage,
}));
const RepositorySettingsPage = lazy(async () => ({
  default: (await import("../features/settings/SettingsPage")).RepositorySettingsPage,
}));

export function App(): React.JSX.Element {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Suspense fallback={<LoadingPage />}>
          <Routes>
            <Route element={<SignInPage />} path="/signin" />
            <Route element={<OAuthCallbackPage />} path="/auth/callback" />
            <Route element={<RequireAuth />}>
              <Route element={<AppShell />}>
                <Route element={<RepositoryListPage />} path="/repositories" />
                <Route element={<RepositoryOverviewPage />} path="/repositories/:repositoryId" />
                <Route
                  element={<QuestionWorkspacePage />}
                  path="/repositories/:repositoryId/workspace"
                />
                <Route
                  element={<RepositorySettingsPage />}
                  path="/repositories/:repositoryId/settings"
                />
                <Route element={<SettingsPage />} path="/settings" />
              </Route>
            </Route>
            <Route element={<Navigate replace to="/repositories" />} path="*" />
          </Routes>
        </Suspense>
      </AuthProvider>
    </BrowserRouter>
  );
}
