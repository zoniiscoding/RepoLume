import { Github, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { Installation, Repository } from "../../api/contracts";
import { useAuth } from "../../auth/useAuth";
import { StatusBadge } from "../../components/StatusBadge";
import { EmptyState, InlineAlert, Panel } from "../../components/ui";
import { shortSha } from "../../utils/format";

export function SettingsPage(): React.JSX.Element {
  const { accessToken, user } = useAuth();
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    const controller = new AbortController();
    void Promise.all([
      api.listRepositories(accessToken, controller.signal),
      api.listInstallations(accessToken, controller.signal),
    ])
      .then(([nextRepositories, nextInstallations]) => {
        setRepositories(nextRepositories);
        setInstallations(nextInstallations);
      })
      .catch(() => setError("Settings data is temporarily unavailable."));
    return () => controller.abort();
  }, [accessToken]);

  return (
    <section className="page settings-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Settings</p>
          <h1>Account and repository access</h1>
          <p>RepoLume uses the server-authorized GitHub App connection for repository access.</p>
        </div>
      </header>
      {error ? <InlineAlert tone="error">{error}</InlineAlert> : null}
      <div className="settings-grid">
        <Panel>
          <h2>Account</h2>
          <dl className="metadata-grid">
            <div>
              <dt>GitHub account</dt>
              <dd>{user?.github_login ?? "Not available"}</dd>
            </div>
            <div>
              <dt>Display name</dt>
              <dd>{user?.display_name ?? "Not provided"}</dd>
            </div>
          </dl>
        </Panel>
        <Panel>
          <h2>GitHub connection</h2>
          <p className="settings-copy">
            <Github aria-hidden="true" size={16} /> Active installations are synchronized through
            GitHub. Installations that are suspended or removed immediately lose access.
          </p>
          {installations.length === 0 ? (
            <EmptyState title="No active installation">
              Install the RepoLume GitHub App, then return to connect repositories.
            </EmptyState>
          ) : (
            <ul className="settings-list">
              {installations.map((installation) => (
                <li key={installation.id}>
                  <span>{installation.account_login}</span>
                  <StatusBadge status={installation.status} />
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      <Panel>
        <h2>Connected repositories</h2>
        {repositories.length === 0 ? (
          <EmptyState title="No connected repositories">
            Select a repository from your authorized GitHub App installation to create its first
            index.
          </EmptyState>
        ) : (
          <div className="settings-list">
            {repositories.map((repository) => (
              <Link key={repository.id} to={`/repositories/${repository.id}`}>
                <span>
                  <strong>{repository.github_full_name}</strong>
                  <small className="mono">
                    {repository.indexed_branch ?? repository.default_branch} ·{" "}
                    {shortSha(repository.active_commit_sha)}
                  </small>
                </span>
                <StatusBadge status={repository.indexing_status} />
              </Link>
            ))}
          </div>
        )}
        <InlineAlert tone="neutral">
          <ShieldCheck aria-hidden="true" size={16} /> Repository removal/disconnect is not exposed
          by the current backend. Manage GitHub App access in GitHub; the server will revoke access
          when it receives the supported event.
        </InlineAlert>
      </Panel>
    </section>
  );
}

export function RepositorySettingsPage(): React.JSX.Element {
  return (
    <section className="page settings-page">
      <header className="page-header">
        <div>
          <p className="eyebrow">Repository settings</p>
          <h1>Repository management</h1>
          <p>Connection state, branch eligibility, and access revocation are server controlled.</p>
        </div>
      </header>
      <InlineAlert tone="neutral">
        The current API supports safe manual reindexing and server-driven revocation. It does not
        expose a browser-driven disconnect or arbitrary branch selection.
      </InlineAlert>
      <Link className="button" to="..">
        Return to repository overview
      </Link>
    </section>
  );
}
