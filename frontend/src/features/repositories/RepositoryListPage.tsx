import { FolderGit2, Globe2, LockKeyhole, Plus, RefreshCw, Search } from "lucide-react";
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
  const [publicUrl, setPublicUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [resultMessage, setResultMessage] = useState<string | null>(null);

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

  async function importPublicRepository(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!accessToken || importing) return;
    if (!/^https:\/\/github\.com\//i.test(publicUrl.trim())) {
      setError("Enter an HTTPS github.com repository URL.");
      return;
    }
    setImporting(true);
    setError(null);
    setResultMessage(null);
    try {
      const result = await api.importPublicRepository(accessToken, publicUrl.trim());
      setConnected((current) => [
        ...current.filter((item) => item.id !== result.repository.id),
        result.repository,
      ]);
      setPublicUrl("");
      setResultMessage(
        result.already_current
          ? `${result.repository.github_full_name} is already current.`
          : result.reused_index
            ? `Attached ${result.repository.github_full_name} and reused its shared index.`
            : `Queued ${result.repository.github_full_name} for indexing.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The repository could not be imported.",
      );
    } finally {
      setImporting(false);
    }
  }

  async function refreshPublicRepository(repository: Repository): Promise<void> {
    if (!accessToken || refreshingId) return;
    setRefreshingId(repository.id);
    setError(null);
    setResultMessage(null);
    try {
      const result = await api.refreshPublicRepository(accessToken, repository.id);
      setConnected((current) =>
        current.map((item) => (item.id === result.repository.id ? result.repository : item)),
      );
      setResultMessage(
        result.already_current
          ? `${result.repository.github_full_name} is already current.`
          : `Refreshing ${result.repository.github_full_name}.`,
      );
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "The repository could not be refreshed.",
      );
    } finally {
      setRefreshingId(null);
    }
  }

  return (
    <section className="page page--repositories">
      <header className="page-header">
        <div>
          <p className="eyebrow">Repositories</p>
          <h1>Choose a repository to understand</h1>
          <p>
            Import a public GitHub repository by URL. Private repositories remain protected by the
            RepoLume GitHub App.
          </p>
        </div>
      </header>
      {error ? <InlineAlert tone="error">{error}</InlineAlert> : null}
      {resultMessage ? <InlineAlert tone="success">{resultMessage}</InlineAlert> : null}
      {loading ? <RepositoryListSkeleton /> : null}
      {!loading ? (
        <Panel className="public-import">
          <div>
            <p className="eyebrow">Public repository</p>
            <h2>Import public repository</h2>
            <p>
              No GitHub App installation is required. RepoLume validates the URL and visibility.
            </p>
          </div>
          <form
            className="public-import__form"
            onSubmit={(event) => void importPublicRepository(event)}
          >
            <label>
              <span>GitHub repository URL</span>
              <Input
                aria-label="GitHub repository URL"
                maxLength={2048}
                placeholder="https://github.com/owner/repository"
                value={publicUrl}
                onChange={(event) => setPublicUrl(event.target.value)}
              />
            </label>
            <Button
              disabled={!publicUrl.trim()}
              loading={importing}
              type="submit"
              variant="primary"
            >
              <Globe2 aria-hidden="true" size={16} />
              Import public repository
            </Button>
          </form>
        </Panel>
      ) : null}
      {!loading && connected.length > 0 ? (
        <Panel className="connected-repositories">
          <div>
            <p className="eyebrow">Your repositories</p>
            <h2>Available to this account</h2>
          </div>
          <div className="repository-card-grid">
            {connected.map((repository) => (
              <article className="repository-card" key={repository.id}>
                <div className="repository-card__heading">
                  <div>
                    <strong>{repository.github_full_name}</strong>
                    <small>{repository.access_source}</small>
                  </div>
                  <span className="repository-visibility">
                    {repository.is_private ? "Private" : "Public"}
                  </span>
                </div>
                <StatusBadge status={repository.indexing_status} />
                <dl>
                  <div>
                    <dt>Branch</dt>
                    <dd className="mono">
                      {repository.indexed_branch ?? repository.default_branch}
                    </dd>
                  </div>
                  <div>
                    <dt>Commit</dt>
                    <dd className="mono">
                      {repository.active_commit_sha?.slice(0, 8) ?? "Not indexed"}
                    </dd>
                  </div>
                </dl>
                <div className="button-row">
                  <Link className="button button--primary" to={`/repositories/${repository.id}`}>
                    Open
                  </Link>
                  {repository.access_mode === "public" ? (
                    <Button
                      loading={refreshingId === repository.id}
                      onClick={() => void refreshPublicRepository(repository)}
                    >
                      <RefreshCw aria-hidden="true" size={15} /> Refresh
                    </Button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        </Panel>
      ) : null}
      {!loading && installations.length === 0 ? (
        <EmptyState title="Connect private repositories">
          Install or configure the RepoLume GitHub App to access private or organization
          repositories. Public URL imports remain available above.
        </EmptyState>
      ) : null}
      {!loading && installations.length > 0 ? (
        <Panel className="repository-browser">
          <div>
            <p className="eyebrow">Private repositories</p>
            <h2>Connect private repositories</h2>
            <p>These repositories are authorized through an active GitHub App installation.</p>
          </div>
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
