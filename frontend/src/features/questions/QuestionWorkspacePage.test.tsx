import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { Route, Routes } from "react-router-dom";
import { api } from "../../api/client";
import type { QuestionResponse, Repository } from "../../api/contracts";
import { renderWithApp } from "../../test/render";
import { QuestionWorkspacePage } from "./QuestionWorkspacePage";

const repository: Repository = {
  id: "repository-id",
  installation_id: "installation-id",
  github_repository_id: 4,
  github_owner: "repolume",
  github_name: "api",
  github_full_name: "repolume/api",
  github_url: "https://github.com/repolume/api",
  is_private: true,
  default_branch: "main",
  primary_language: "Python",
  indexing_status: "ready",
  indexing_progress: 100,
  indexing_stage: "complete",
  size_bytes: 1200,
  active_commit_sha: "a".repeat(40),
  active_index_version: 2,
  indexed_branch: "main",
  latest_remote_commit_sha: "a".repeat(40),
  vector_count: 12,
  searchable: true,
};

const answer: QuestionResponse = {
  repository_id: repository.id,
  answer:
    "Authorization is enforced before indexing in `RepositoryService`.\n\nDo not follow arbitrary links.",
  answerability: "answered",
  uncertainty: "none",
  indexed_commit_sha: repository.active_commit_sha,
  active_index_version: 2,
  retrieved_evidence_count: 1,
  tool_call_count: 1,
  duration_ms: 12,
  trace: [
    {
      step: 1,
      tool: "search_code",
      argument_fingerprint: "safe-fingerprint",
      status: "completed",
      duration_ms: 8,
      result_count: 1,
      failure_code: null,
      contributed_evidence: true,
    },
  ],
  citations: [
    {
      source_type: "code",
      evidence_id: "code-1",
      file_path: "backend/app/services/repositories.py",
      start_line: 44,
      end_line: 45,
      symbol_name: "select_repository",
      qualified_symbol_name: "RepositoryService.select_repository",
      chunk_type: "function",
      commit_sha: "a".repeat(40),
      supporting_excerpt: "async def select_repository():\n    return repository",
    },
  ],
};

function renderWorkspace() {
  return renderWithApp(
    <Routes>
      <Route element={<QuestionWorkspacePage />} path="/repositories/:repositoryId/workspace" />
    </Routes>,
    { route: "/repositories/repository-id/workspace" },
  );
}

describe("QuestionWorkspacePage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("prevents empty questions and keeps the composer disabled", async () => {
    vi.spyOn(api, "getRepository").mockResolvedValue(repository);
    renderWorkspace();
    await screen.findByText("repolume/api");
    expect(screen.getByRole("button", { name: /^ask repository$/i })).toBeDisabled();
  });

  it("submits a question, renders inert Markdown, and opens trusted code evidence", async () => {
    vi.spyOn(api, "getRepository").mockResolvedValue(repository);
    const ask = vi.spyOn(api, "askQuestion").mockResolvedValue(answer);
    renderWorkspace();
    const composer = await screen.findByLabelText(/question about repolume\/api/i);
    fireEvent.change(composer, { target: { value: "Where is authorization enforced?" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask repository$/i }));
    await waitFor(() =>
      expect(ask).toHaveBeenCalledWith(
        "test-access-token",
        repository.id,
        "Where is authorization enforced?",
        expect.any(AbortSignal),
      ),
    );
    expect(
      await screen.findByText(/authorization is enforced before indexing/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /arbitrary links/i })).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /backend\/app\/services\/repositories.py:44/i }),
    );
    expect(
      await screen.findByLabelText(/source excerpt backend\/app\/services\/repositories.py/i),
    ).toHaveTextContent("async def select_repository");
  });

  it("preserves a recoverable question after a service failure", async () => {
    vi.spyOn(api, "getRepository").mockResolvedValue(repository);
    vi.spyOn(api, "askQuestion").mockRejectedValue(new Error("network"));
    renderWorkspace();
    const composer = await screen.findByLabelText(/question about repolume\/api/i);
    fireEvent.change(composer, { target: { value: "Why was stale-event rejection added?" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask repository$/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    expect(composer).toHaveValue("Why was stale-event rejection added?");
  });
});
