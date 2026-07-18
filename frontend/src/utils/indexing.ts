import type { RepositoryIndexingStatus } from "../api/contracts";

const ACTIVE_STATUSES = new Set<RepositoryIndexingStatus>([
  "queued",
  "cloning",
  "discovering",
  "parsing",
  "building_graph",
  "embedding",
  "finalizing",
]);

export function isIndexingStatus(status: RepositoryIndexingStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}
