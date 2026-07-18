import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { IndexingStatusPanel } from "./IndexingStatusPanel";
import { renderWithApp } from "../../test/render";
import type { IndexingStatus } from "../../api/contracts";

const status: IndexingStatus = {
  repository_id: "repo",
  repository_status: "embedding",
  job_id: "job",
  job_status: "running",
  attempt: 1,
  progress: 88,
  stage: "embedding",
  error_code: null,
  safe_error_message: null,
  discovered_file_count: 4,
  discovered_total_bytes: 200,
  skipped_file_counts: {},
  parsed_file_count: 4,
  partial_file_count: 0,
  parser_skipped_file_count: 0,
  symbol_count: 7,
  chunk_count: 9,
  parser_warning_counts: {},
  call_site_count: 3,
  exact_edge_count: 2,
  ambiguous_edge_count: 0,
  unresolved_call_count: 1,
  graph_warning_count: 0,
  target_index_version: 2,
  embedded_chunk_count: 5,
  vector_count: 5,
  active_vector_count: 4,
  embedding_failed_count: 0,
  embedding_skipped_count: 0,
  active_commit_sha: "a".repeat(40),
  active_index_version: 1,
  searchable: true,
  last_failure_category: null,
  heartbeat_at: null,
  completed_at: null,
  requested_mode: "incremental",
  actual_mode: "incremental",
  full_rebuild_reason: null,
  changed_file_count: 2,
  changed_file_counts: { modified: 2 },
  reused_chunk_count: 3,
  reembedded_chunk_count: 2,
  graph_rebuilt: true,
  indexed_branch: "main",
  latest_remote_commit_sha: "b".repeat(40),
  last_delivery_status: "processing",
  last_delivery_at: null,
};

describe("IndexingStatusPanel", () => {
  it("explains that the prior active index remains available during a replacement", () => {
    renderWithApp(<IndexingStatusPanel status={status} />);
    expect(screen.getByText(/last validated index stays available/i)).toBeInTheDocument();
    expect(screen.getByText("Reused chunks")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows a safe failure message", () => {
    renderWithApp(
      <IndexingStatusPanel
        status={{
          ...status,
          repository_status: "failed",
          error_code: "embedding_unavailable",
          safe_error_message: "Embedding service is temporarily unavailable",
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Embedding service is temporarily unavailable",
    );
  });
});
