import { ChevronDown, FolderGit2, LayoutDashboard, LogOut, Menu, Settings, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Repository } from "../api/contracts";
import { useAuth } from "../auth/useAuth";
import { StatusBadge } from "../components/StatusBadge";
import { Button } from "../components/ui";

function SidebarLink({
  to,
  icon,
  children,
}: {
  to: string;
  icon: React.ReactNode;
  children: string;
}): React.JSX.Element {
  return (
    <NavLink
      className={({ isActive }) => `sidebar-link${isActive ? " sidebar-link--active" : ""}`}
      to={to}
    >
      {icon}
      <span>{children}</span>
    </NavLink>
  );
}

export function AppShell(): React.JSX.Element {
  const { accessToken, user, signOut } = useAuth();
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!accessToken) return;
    const controller = new AbortController();
    void api
      .listRepositories(accessToken, controller.signal)
      .then(setRepositories)
      .catch(() => setRepositories([]));
    return () => controller.abort();
  }, [accessToken]);

  async function handleSignOut(): Promise<void> {
    await signOut();
    navigate("/signin", { replace: true });
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <button
        aria-label="Open navigation"
        className="mobile-menu-button"
        onClick={() => setOpen(true)}
      >
        <Menu size={20} />
      </button>
      <aside className={`sidebar${open ? " sidebar--open" : ""}`} aria-label="Primary navigation">
        <div className="sidebar__top">
          <Link className="brand" to="/repositories" onClick={() => setOpen(false)}>
            <span className="brand__mark" aria-hidden="true">
              R
            </span>
            <span>RepoLume</span>
          </Link>
          <button
            aria-label="Close navigation"
            className="mobile-close-button"
            onClick={() => setOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <nav className="sidebar__nav">
          <SidebarLink icon={<LayoutDashboard size={17} />} to="/repositories">
            Repositories
          </SidebarLink>
          <SidebarLink icon={<Settings size={17} />} to="/settings">
            Settings
          </SidebarLink>
        </nav>
        <div className="sidebar__section">
          <p className="sidebar__label">Connected repositories</p>
          {repositories.length === 0 ? (
            <p className="sidebar__empty">No connected repositories</p>
          ) : null}
          {repositories.slice(0, 8).map((repository) => (
            <NavLink
              key={repository.id}
              className={({ isActive }) =>
                `repository-link${isActive ? " repository-link--active" : ""}`
              }
              to={`/repositories/${repository.id}`}
              title={repository.github_full_name}
              onClick={() => setOpen(false)}
            >
              <FolderGit2 aria-hidden="true" size={15} />
              <span>{repository.github_full_name}</span>
              <StatusBadge status={repository.indexing_status} />
            </NavLink>
          ))}
        </div>
        <div className="sidebar__user">
          <div className="user-summary">
            {user?.avatar_url ? (
              <img alt="" src={user.avatar_url} />
            ) : (
              <span className="avatar-fallback">
                {(user?.github_login ?? user?.display_name ?? "R").slice(0, 1).toUpperCase()}
              </span>
            )}
            <span>{user?.github_login ?? user?.display_name ?? "RepoLume user"}</span>
            <ChevronDown aria-hidden="true" size={14} />
          </div>
          <Button className="sidebar__logout" variant="quiet" onClick={() => void handleSignOut()}>
            <LogOut aria-hidden="true" size={16} />
            Sign out
          </Button>
        </div>
      </aside>
      {open ? (
        <button
          aria-label="Close navigation overlay"
          className="sidebar-scrim"
          onClick={() => setOpen(false)}
        />
      ) : null}
      <main id="main-content" className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
