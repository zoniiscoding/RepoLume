import { FolderGit2, LockKeyhole, Plus, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { AvailableRepository, Installation, Repository } from "../../api/contracts";
import { useAuth } from "../../auth/useAuth";
import { StatusBadge } from "../../components/StatusBadge";
import { Button, EmptyState, InlineAlert, Input, Panel, Skeleton } from "../../components/ui";

export function RepositoryListPage(): React.JSX.Element {
  const { accessToken } = useAuth();
  const [installations, setInstallations] = useState<Installation[]>([]);
  const [available, setAvailable] = useState<AvailableRepository[]>([]);
  const [connected, setConnected] = useState<Repository[]>([]);
  const [installationId, setInstallationId] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [connectingId, setConnectingId] = useState<number | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    const controller = new AbortController();
    void Promise.all([
      api.listInstallations(accessToken, controller.signal),
      api.listRepositories(accessToken, controller.signal),
    ])
      .then(([nextInstallations, nextConnected]) => {
        setInstallations(nextInstallations);
        setConnected(nextConnected);
        setInstallationId(nextInstallations.find((item) => item.status === "active")?.id ?? "");
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("Repositories are temporarily unavailable.");
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [accessToken]);

  useEffect(() => {
    if (!accessToken || !installationId) {
      setAvailable([]);
      return;
    }
    const controller = new AbortController();
    void api
      .listInstallationRepositories(accessToken, installationId, controller.signal)
      .then(setAvailable)
      .catch(() => {
        if (!controller.signal.aborted) {
          setError("GitHub repository access is temporarily unavailable.");
        }
      });
    return () => controller.abort();
  }, [accessToken, installationId]);

  const connectedByGitHubId = useMemo(
    () => new Map(connected.map((item) => [item.github_repository_id, item])),
    [connected],
  );
  const filtered = available.filter((repository) =>
    repository.github_full_name.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()),
  );

  async function connect(repository: AvailableRepository): Promise<void> {
    if (!accessToken || !installationId) return;
    setConnectingId(repository.github_repository_id);
    setError(null);
    try {
      const result = await api.connectRepository(
        accessToken,
        installationId,
        repository.github_repository_id,
      );
      setConnected((current) => [
        ...current.filter((item) => item.id !== result.repository.id),
        result.repository,
      ]);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The repository could not be connected.",
      );
    } finally {
      setConnectingId(null);
    }
  }

  return (
    <section className="page page--repositories">
      <header className="page-header">
        <div>
          <p className="eyebrow">Repositories</p>
          <h1>Connect an authorized repository</h1>
          <p>
            RepoLume only shows repositories available through your active GitHub App installation.
          </p>
        </div>
      </header>
      {error ? <InlineAlert tone="error">{error}</InlineAlert> : null}
      {loading ? <RepositoryListSkeleton /> : null}
      {!loading && installations.length === 0 ? (
        <EmptyState title="No GitHub App installation">
          Install the RepoLume GitHub App for an account or organization, then return here to
          connect a repository.
        </EmptyState>
      ) : null}
      {!loading && installations.length > 0 ? (
        <Panel className="repository-browser">
          <div className="repository-browser__toolbar">
            <label className="select-label">
              <span>Installation</span>
              <select
                value={installationId}
                onChange={(event) => setInstallationId(event.target.value)}
              >
                {installations.map((installation) => (
                  <option key={installation.id} value={installation.id}>
                    {installation.account_login} · {installation.status}
                  </option>
                ))}
              </select>
            </label>
            <label className="search-input">
              <Search aria-hidden="true" size={16} />
              <span className="sr-only">Search repositories</span>
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search authorized repositories"
              />
            </label>
          </div>
          <div className="repository-table" role="table" aria-label="Authorized repositories">
            <div className="repository-table__head" role="row">
              <span role="columnheader">Repository</span>
              <span role="columnheader">Branch</span>
              <span role="columnheader">Status</span>
              <span role="columnheader">Action</span>
            </div>
            {filtered.map((repository) => {
              const existing = connectedByGitHubId.get(repository.github_repository_id);
              return (
                <div
                  className="repository-table__row"
                  key={repository.github_repository_id}
                  role="row"
                >
                  <div role="cell" className="repository-name">
                    <FolderGit2 aria-hidden="true" size={17} />
                    <span>
                      <strong>{repository.github_full_name}</strong>
                      <small>
                        {repository.primary_language ?? "Repository"} ·{" "}
                        {repository.is_private ? "Private" : "Public"}
                      </small>
                    </span>
                  </div>
                  <span role="cell" className="mono">
                    {repository.default_branch}
                  </span>
                  <span role="cell">
                    {existing ? (
                      <StatusBadge status={existing.indexing_status} />
                    ) : (
                      <span className="muted">Not connected</span>
                    )}
                  </span>
                  <span role="cell">
                    {existing ? (
                      <Link className="button" to={`/repositories/${existing.id}`}>
                        Open
                      </Link>
                    ) : (
                      <Button
                        loading={connectingId === repository.github_repository_id}
                        variant="primary"
                        onClick={() => void connect(repository)}
                      >
                        <Plus aria-hidden="true" size={16} />
                        Connect
                      </Button>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
          {filtered.length === 0 ? (
            <EmptyState title={query ? "No matching repositories" : "No authorized repositories"}>
              {query
                ? "Try a different repository name."
                : "The selected installation has no repositories RepoLume can access."}
            </EmptyState>
          ) : null}
        </Panel>
      ) : null}
      {connected.length > 0 ? (
        <InlineAlert tone="neutral">
          <LockKeyhole aria-hidden="true" size={16} /> Connection and indexing stay
          server-authorized; this browser cannot choose a branch or index version.
        </InlineAlert>
      ) : null}
    </section>
  );
}

function RepositoryListSkeleton(): React.JSX.Element {
  return (
    <Panel className="repository-browser">
      <Skeleton className="skeleton--toolbar" />
      <Skeleton className="skeleton--row" />
      <Skeleton className="skeleton--row" />
      <Skeleton className="skeleton--row" />
    </Panel>
  );
}
