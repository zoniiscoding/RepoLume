export type InstallationStatus = "active" | "suspended" | "deleted";

export type RepositoryIndexingStatus =
  | "not_indexed"
  | "queued"
  | "cloning"
  | "discovering"
  | "parsing"
  | "building_graph"
  | "embedding"
  | "finalizing"
  | "ready"
  | "failed"
  | "access_revoked"
  | "deleting";

export type IndexingJobStatus =
  | "queued"
  | "running"
  | "retrying"
  | "completed"
  | "failed"
  | "cancelled";

export type Answerability =
  | "answered"
  | "partially_answered"
  | "insufficient_evidence"
  | "unsupported_question"
  | "temporarily_unavailable";

export interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
}

export interface User {
  id: string;
  github_user_id: number;
  github_login: string;
  display_name: string | null;
  avatar_url: string | null;
  email: string | null;
}

export interface AccessTokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface AuthenticationResponse extends AccessTokenResponse {
  user: User;
}

export interface Installation {
  id: string;
  github_installation_id: number;
  account_type: string;
  account_login: string;
  status: InstallationStatus;
  repository_selection: string;
}

export interface AvailableRepository {
  id: string;
  github_repository_id: number;
  github_owner: string;
  github_name: string;
  github_full_name: string;
  github_url: string;
  is_private: boolean;
  default_branch: string;
  primary_language: string | null;
}

export interface Repository {
  id: string;
  installation_id: string;
  github_repository_id: number;
  github_owner: string;
  github_name: string;
  github_full_name: string;
  github_url: string;
  is_private: boolean;
  default_branch: string;
  primary_language: string | null;
  indexing_status: RepositoryIndexingStatus;
  indexing_progress: number;
  indexing_stage: string | null;
  size_bytes: number | null;
  active_commit_sha: string | null;
  active_index_version: number;
  indexed_branch: string | null;
  latest_remote_commit_sha: string | null;
  vector_count: number;
  searchable: boolean;
}

export interface IndexingStatus {
  repository_id: string;
  repository_status: RepositoryIndexingStatus;
  job_id: string | null;
  job_status: IndexingJobStatus | null;
  attempt: number;
  progress: number;
  stage: string | null;
  error_code: string | null;
  safe_error_message: string | null;
  discovered_file_count: number;
  discovered_total_bytes: number;
  skipped_file_counts: Record<string, number>;
  parsed_file_count: number;
  partial_file_count: number;
  parser_skipped_file_count: number;
  symbol_count: number;
  chunk_count: number;
  parser_warning_counts: Record<string, number>;
  call_site_count: number;
  exact_edge_count: number;
  ambiguous_edge_count: number;
  unresolved_call_count: number;
  graph_warning_count: number;
  target_index_version: number | null;
  embedded_chunk_count: number;
  vector_count: number;
  active_vector_count: number;
  embedding_failed_count: number;
  embedding_skipped_count: number;
  active_commit_sha: string | null;
  active_index_version: number;
  searchable: boolean;
  last_failure_category: string | null;
  heartbeat_at: string | null;
  completed_at: string | null;
  requested_mode: "incremental" | "full" | null;
  actual_mode: "incremental" | "full" | null;
  full_rebuild_reason: string | null;
  changed_file_count: number;
  changed_file_counts: Record<string, number>;
  reused_chunk_count: number;
  reembedded_chunk_count: number;
  graph_rebuilt: boolean;
  indexed_branch: string | null;
  latest_remote_commit_sha: string | null;
  last_delivery_status: string | null;
  last_delivery_at: string | null;
}

export interface RepositoryJobResponse {
  repository: Repository;
  job: IndexingStatus;
}

export interface CodeCitation {
  source_type: "code";
  evidence_id: string;
  file_path: string;
  start_line: number;
  end_line: number;
  symbol_name: string | null;
  qualified_symbol_name: string | null;
  chunk_type: string;
  commit_sha: string;
  supporting_excerpt: string;
}

export interface CommitCitation {
  source_type: "commit";
  evidence_id: string;
  commit_sha: string;
  message: string;
  committed_at: string;
  author_login: string | null;
  parent_shas: string[];
  changed_paths: string[];
  patch_excerpt: string | null;
  html_url: string;
}

export interface PullRequestCitation {
  source_type: "pull_request";
  evidence_id: string;
  number: number;
  title: string;
  state: string;
  author_login: string | null;
  merged_at: string | null;
  merge_commit_sha: string | null;
  changed_paths: string[];
  body_excerpt: string | null;
  html_url: string;
}

export interface CallerCitation {
  source_type: "caller";
  evidence_id: string;
  target_symbol_name: string;
  target_qualified_name: string;
  target_file_path: string;
  caller_symbol_name: string;
  caller_qualified_name: string;
  caller_file_path: string;
  caller_start_line: number;
  caller_end_line: number;
  call_line: number;
  call_end_line: number;
  call_expression: string;
  resolution_type: string;
  confidence: string;
  commit_sha: string;
  index_version: number;
  limitation: string;
}

export type Citation = CodeCitation | CommitCitation | PullRequestCitation | CallerCitation;

export interface AgentTraceStep {
  step: number;
  tool: string;
  argument_fingerprint: string;
  status: string;
  duration_ms: number;
  result_count: number;
  failure_code: string | null;
  contributed_evidence: boolean;
}

export interface QuestionResponse {
  repository_id: string;
  answer: string;
  answerability: Answerability;
  uncertainty: string;
  citations: Citation[];
  indexed_commit_sha: string | null;
  active_index_version: number;
  retrieved_evidence_count: number;
  tool_call_count: number;
  duration_ms: number;
  trace: AgentTraceStep[];
}
