import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { IndexingStatus } from "../../api/contracts";
import { isIndexingStatus } from "../../utils/indexing";

interface Result {
  status: IndexingStatus | null;
  loading: boolean;
  error: string | null;
  refresh(): void;
}

export function useIndexingStatus(
  accessToken: string | null,
  repositoryId: string | undefined,
): Result {
  const [status, setStatus] = useState<IndexingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    if (!accessToken || !repositoryId) return;
    let stopped = false;
    let inFlight = false;
    let timeout: number | undefined;
    const controller = new AbortController();

    const load = async (): Promise<void> => {
      if (stopped || inFlight || document.hidden) return;
      inFlight = true;
      try {
        const next = await api.getRepositoryStatus(accessToken, repositoryId, controller.signal);
        if (stopped) return;
        setStatus(next);
        setError(null);
        setLoading(false);
        if (isIndexingStatus(next.repository_status) && !document.hidden) {
          timeout = window.setTimeout(() => void load(), 5000);
        }
      } catch {
        if (!stopped) {
          setError("Indexing status is temporarily unavailable.");
          setLoading(false);
          timeout = window.setTimeout(() => void load(), 10_000);
        }
      } finally {
        inFlight = false;
      }
    };
    const resumeWhenVisible = (): void => {
      if (!document.hidden) void load();
    };
    document.addEventListener("visibilitychange", resumeWhenVisible);
    void load();
    return () => {
      stopped = true;
      controller.abort();
      document.removeEventListener("visibilitychange", resumeWhenVisible);
      if (timeout) window.clearTimeout(timeout);
    };
  }, [accessToken, repositoryId, revision]);

  return { status, loading, error, refresh: () => setRevision((current) => current + 1) };
}
