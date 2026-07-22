import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { AvailableRepository, Installation, Repository } from "../../api/contracts";
import { renderWithApp } from "../../test/render";
import { RepositoryListPage } from "./RepositoryListPage";

const installation: Installation = {
  id: "installation-id",
  github_installation_id: 5,
  account_type: "Organization",
  account_login: "octo-org",
  status: "active",
  repository_selection: "selected",
};

const available: AvailableRepository = {
  id: "available-id",
  github_repository_id: 9,
  github_owner: "octo-org",
  github_name: "internal-platform",
  github_full_name: "octo-org/internal-platform",
  github_url: "https://github.com/octo-org/internal-platform",
  is_private: true,
  default_branch: "main",
  primary_language: "Python",
};

const connected: Repository = {
  ...available,
  id: "repository-id",
  installation_id: installation.id,
  access_mode: "github_installation",
  access_source: "GitHub App",
  indexing_status: "queued",
  indexing_progress: 0,
  indexing_stage: "queued",
  size_bytes: null,
  active_commit_sha: null,
  active_index_version: 0,
  indexed_branch: null,
  latest_remote_commit_sha: null,
  vector_count: 0,
  searchable: false,
};

describe("RepositoryListPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("connects only an API-authorized repository and replaces its action with Open", async () => {
    vi.spyOn(api, "listInstallations").mockResolvedValue([installation]);
    vi.spyOn(api, "listRepositories").mockResolvedValue([]);
    vi.spyOn(api, "listInstallationRepositories").mockResolvedValue([available]);
    const connect = vi.spyOn(api, "connectRepository").mockResolvedValue({
      repository: connected,
      job: {} as never,
    });
    renderWithApp(<RepositoryListPage />);

    expect(await screen.findByText("octo-org/internal-platform")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /connect/i }));
    await waitFor(() =>
      expect(connect).toHaveBeenCalledWith("test-access-token", installation.id, 9),
    );
    expect((await screen.findAllByRole("link", { name: "Open" }))[0]).toHaveAttribute(
      "href",
      "/repositories/repository-id",
    );
  });

  it("reports an intentional no-installation state", async () => {
    vi.spyOn(api, "listInstallations").mockResolvedValue([]);
    vi.spyOn(api, "listRepositories").mockResolvedValue([]);
    renderWithApp(<RepositoryListPage />);

    expect(await screen.findByText(/connect private repositories/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /import public repository/i })).toBeDisabled();
  });

  it("imports a public URL and exposes shared-index refresh controls", async () => {
    vi.spyOn(api, "listInstallations").mockResolvedValue([]);
    vi.spyOn(api, "listRepositories").mockResolvedValue([]);
    const publicRepository: Repository = {
      ...connected,
      id: "public-repository-id",
      installation_id: null,
      access_mode: "public",
      access_source: "Public URL",
      github_full_name: "octocat/public-repo",
      github_name: "public-repo",
      github_url: "https://github.com/octocat/public-repo",
      is_private: false,
    };
    const importRepository = vi.spyOn(api, "importPublicRepository").mockResolvedValue({
      repository: publicRepository,
      job: {} as never,
      already_current: false,
      reused_index: false,
    });
    renderWithApp(<RepositoryListPage />);

    const input = await screen.findByLabelText(/github repository url/i);
    fireEvent.change(input, { target: { value: "https://github.com/octocat/public-repo" } });
    fireEvent.click(screen.getByRole("button", { name: /import public repository/i }));

    await waitFor(() =>
      expect(importRepository).toHaveBeenCalledWith(
        "test-access-token",
        "https://github.com/octocat/public-repo",
      ),
    );
    expect(await screen.findByText("Public URL")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
  });
});
