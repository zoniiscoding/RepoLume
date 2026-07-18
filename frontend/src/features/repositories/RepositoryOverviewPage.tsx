import { ArrowRight, GitBranch, GitCommitHorizontal, RefreshCw, Settings2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, api } from "../../api/client";
import type { Repository } from "../../api/contracts";
import { useAuth } from "../../auth/useAuth";
import { StatusBadge } from "../../components/StatusBadge";
import { Button, EmptyState, InlineAlert, Panel, Skeleton } from "../../components/ui";
import { IndexingStatusPanel } from "../indexing/IndexingStatusPanel";
import { useIndexingStatus } from "../indexing/useIndexingStatus";
import { formatBytes, shortSha } from "../../utils/format";

const suggestions = [
  "Where is repository authorization enforced before indexing?",
  "What calls IndexingWorker.process_job?",
  "Why was stale-event rejection added?",
];

export function RepositoryOverviewPage(): React.JSX.Element {
  const { repositoryId } = useParams();
  const { accessToken } = useAuth();
  const [repository, setRepository] = useState<Repository | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const {
    status,
    loading: statusLoading,
    error: statusError,
    refresh,
  } = useIndexingStatus(accessToken, repositoryId);

  useEffect(() => {
    if (!accessToken || !repositoryId) return;
    const controller = new AbortController();
    void api
      .getRepository(accessToken, repositoryId, controller.signal)
      .then(setRepository)
      .catch(() => setError("This repository is no longer available."))
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [accessToken, repositoryId]);

  async function reindex(): Promise<void> {
    if (!accessToken || !repositoryId) return;
    setReindexing(true);
    setError(null);
    try {
      const result = await api.reindexRepository(accessToken, repositoryId);
      setRepository(result.repository);
      refresh();
      setDialogOpen(false);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The repository could not be queued for reindexing.",
      );
    } finally {
      setReindexing(false);
    }
  }

  if (loading) return <OverviewSkeleton />;
  if (!repository)
    return (
      <EmptyState title="Repository unavailable">
        {error ?? "The repository could not be loaded."}
      </EmptyState>
    );

  return (
    <section className="page repository-overview">
      <RepositoryContext repository={repository} />
      {error ? <InlineAlert tone="error">{error}</InlineAlert> : null}
      {statusError ? <InlineAlert tone="warning">{statusError}</InlineAlert> : null}
      <header className="overview-header">
        <div>
          <p className="eyebrow">Repository workspace</p>
          <h1>{repository.github_full_name}</h1>
          <div className="overview-header__meta">
            <StatusBadge status={repository.indexing_status} />
            <span>{repository.is_private ? "Private" : "Public"}</span>
            <span>{repository.primary_language ?? "Repository"}</span>
          </div>
        </div>
        <div className="button-row">
          <Link className="button button--primary" to={`/repositories/${repository.id}/workspace`}>
            Ask a question <ArrowRight aria-hidden="true" size={16} />
          </Link>
          <Button onClick={() => setDialogOpen(true)}>
            <RefreshCw aria-hidden="true" size={16} />
            Reindex
          </Button>
          <Link
            aria-label="Repository settings"
            className="button"
            to={`/repositories/${repository.id}/settings`}
          >
            <Settings2 aria-hidden="true" size={16} />
          </Link>
        </div>
      </header>
      <div className="overview-grid">
        <Panel className="repository-facts">
          <h2>Current index</h2>
          <Fact
            icon={<GitBranch size={16} />}
            label="Indexed branch"
            value={repository.indexed_branch ?? repository.default_branch}
            mono
          />
          <Fact
            icon={<GitCommitHorizontal size={16} />}
            label="Active commit"
            value={shortSha(repository.active_commit_sha)}
            mono
          />
          <Fact label="Active vectors" value={repository.vector_count.toLocaleString()} />
          <Fact label="Repository size" value={formatBytes(repository.size_bytes)} />
        </Panel>
        <Panel className="suggestions">
          <h2>Suggested questions</h2>
          <p>
            Ask only about the active indexed repository. Answers are grounded in current evidence.
          </p>
          <ul>
            {suggestions.map((question) => (
              <li key={question}>
                <Link
                  to={`/repositories/${repository.id}/workspace?question=${encodeURIComponent(question)}`}
                >
                  {question}
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      {statusLoading ? (
        <OverviewSkeleton />
      ) : status ? (
        <IndexingStatusPanel status={status} />
      ) : null}
      {dialogOpen ? (
        <ReindexDialog
          loading={reindexing}
          onCancel={() => setDialogOpen(false)}
          onConfirm={() => void reindex()}
        />
      ) : null}
    </section>
  );
}

function RepositoryContext({ repository }: { repository: Repository }): React.JSX.Element {
  return (
    <div className="repository-context">
      <span className="repository-context__name">{repository.github_full_name}</span>
      <span>{repository.is_private ? "Private" : "Public"}</span>
      <span className="mono">{repository.indexed_branch ?? repository.default_branch}</span>
      <span className="mono">{shortSha(repository.active_commit_sha)}</span>
      <StatusBadge status={repository.indexing_status} />
    </div>
  );
}

function Fact({
  icon,
  label,
  value,
  mono = false,
}: {
  icon?: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
}): React.JSX.Element {
  return (
    <div className="fact">
      <dt>
        {icon}
        {label}
      </dt>
      <dd className={mono ? "mono" : ""}>{value}</dd>
    </div>
  );
}

function ReindexDialog({
  loading,
  onCancel,
  onConfirm,
}: {
  loading: boolean;
  onCancel(): void;
  onConfirm(): void;
}): React.JSX.Element {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocus.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    confirmRef.current?.focus();
    return () => previousFocus.current?.focus();
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLElement>): void {
    if (event.key === "Escape" && !loading) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (event.key !== "Tab") return;
    event.preventDefault();
    if (document.activeElement === confirmRef.current) cancelRef.current?.focus();
    else confirmRef.current?.focus();
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        aria-describedby="reindex-description"
        aria-labelledby="reindex-title"
        aria-modal="true"
        className="dialog"
        onKeyDown={onKeyDown}
        role="dialog"
      >
        <h2 id="reindex-title">Queue a full reindex?</h2>
        <p id="reindex-description">
          The current active index remains available until the replacement validates. This does not
          change branch selection.
        </p>
        <div className="button-row">
          <Button ref={confirmRef} variant="primary" loading={loading} onClick={onConfirm}>
            Queue full reindex
          </Button>
          <Button ref={cancelRef} disabled={loading} onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </section>
    </div>
  );
}

function OverviewSkeleton(): React.JSX.Element {
  return (
    <section className="page">
      <Skeleton className="skeleton--title" />
      <div className="overview-grid">
        <Skeleton className="skeleton--card" />
        <Skeleton className="skeleton--card" />
      </div>
    </section>
  );
}
