import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

const REPOSITORY_ID = "11111111-1111-1111-1111-111111111111";
const INSTALLATION_ID = "22222222-2222-2222-2222-222222222222";
const LONG_REPOSITORY_NAME = `octocat/${"repository-".repeat(12)}intelligence`;
const LONG_PATH = `src/${"deeply-nested/".repeat(12)}authorization_boundary.py`;

const repository = {
  id: REPOSITORY_ID,
  installation_id: INSTALLATION_ID,
  github_repository_id: 9001,
  github_owner: "octocat",
  github_name: LONG_REPOSITORY_NAME.split("/")[1],
  github_full_name: LONG_REPOSITORY_NAME,
  github_url: "https://github.com/octocat/repolume",
  is_private: true,
  default_branch: "main",
  primary_language: "Python",
  indexing_status: "ready",
  indexing_progress: 100,
  indexing_stage: "complete",
  size_bytes: 3_145_728,
  active_commit_sha: "aabbccddeeff00112233445566778899aabbccdd",
  active_index_version: 4,
  indexed_branch: "main",
  latest_remote_commit_sha: "aabbccddeeff00112233445566778899aabbccdd",
  vector_count: 42_000,
  searchable: true,
};

const indexingStatus = {
  repository_id: REPOSITORY_ID,
  repository_status: "ready",
  job_id: "33333333-3333-3333-3333-333333333333",
  job_status: "completed",
  attempt: 1,
  progress: 100,
  stage: "complete",
  error_code: null,
  safe_error_message: null,
  discovered_file_count: 32,
  discovered_total_bytes: 100_000,
  skipped_file_counts: {},
  parsed_file_count: 30,
  partial_file_count: 0,
  parser_skipped_file_count: 0,
  symbol_count: 120,
  chunk_count: 400,
  parser_warning_counts: {},
  call_site_count: 30,
  exact_edge_count: 25,
  ambiguous_edge_count: 2,
  unresolved_call_count: 3,
  graph_warning_count: 0,
  target_index_version: 4,
  embedded_chunk_count: 400,
  vector_count: 42_000,
  active_vector_count: 42_000,
  embedding_failed_count: 0,
  embedding_skipped_count: 0,
  active_commit_sha: repository.active_commit_sha,
  active_index_version: 4,
  searchable: true,
  last_failure_category: null,
  heartbeat_at: "2026-07-19T00:00:00Z",
  completed_at: "2026-07-19T00:00:00Z",
  requested_mode: "full",
  actual_mode: "full",
  full_rebuild_reason: null,
  changed_file_count: 4,
  changed_file_counts: { added: 2, modified: 2 },
  reused_chunk_count: 120,
  reembedded_chunk_count: 30,
  graph_rebuilt: true,
  indexed_branch: "main",
  latest_remote_commit_sha: repository.latest_remote_commit_sha,
  last_delivery_status: "accepted",
  last_delivery_at: "2026-07-19T00:00:00Z",
};

const answer = {
  repository_id: REPOSITORY_ID,
  answer:
    `# Grounded answer\n\n${"Evidence-backed detail. ".repeat(120)}\n\n` +
    "[unsafe navigation](javascript:alert(1)) <img src=x onerror=alert(1)>",
  answerability: "partially_answered",
  uncertainty: "The active index does not contain runtime-only relationships.",
  citations: [
    {
      source_type: "code",
      evidence_id: "code-1",
      file_path: LONG_PATH,
      start_line: 12,
      end_line: 15,
      symbol_name: "authorize_repository",
      qualified_symbol_name: "services.authorization.authorize_repository",
      chunk_type: "function",
      commit_sha: repository.active_commit_sha,
      supporting_excerpt: "def authorize_repository():\n    return True\n\n# inert source data",
    },
    {
      source_type: "commit",
      evidence_id: "commit-1",
      commit_sha: repository.active_commit_sha,
      message: "Enforce authorization before indexing",
      committed_at: "2026-07-18T00:00:00Z",
      author_login: "octocat",
      parent_shas: ["11223344556677889900aabbccddeeff11223344"],
      changed_paths: [LONG_PATH],
      patch_excerpt: "- unsafe path\n+ validated repository authorization",
      html_url:
        "https://github.com/octocat/repolume/commit/aabbccddeeff00112233445566778899aabbccdd",
    },
    {
      source_type: "pull_request",
      evidence_id: "pr-1",
      number: 77,
      title: "Keep evidence scoped to active indexes",
      state: "closed",
      author_login: "octocat",
      merged_at: "2026-07-18T00:00:00Z",
      merge_commit_sha: repository.active_commit_sha,
      changed_paths: [LONG_PATH],
      body_excerpt: "The link below is intentionally untrusted in this fixture.",
      html_url: "https://untrusted.example.invalid/pull/77",
    },
    {
      source_type: "caller",
      evidence_id: "caller-1",
      target_symbol_name: "authorize_repository",
      target_qualified_name: "services.authorization.authorize_repository",
      target_file_path: LONG_PATH,
      caller_symbol_name: "process_delivery",
      caller_qualified_name: "indexing.worker.process_delivery",
      caller_file_path: "app/indexing/worker.py",
      caller_start_line: 100,
      caller_end_line: 130,
      call_line: 111,
      call_end_line: 111,
      call_expression: "await authorize_repository(context)",
      resolution_type: "exact",
      confidence: "high",
      commit_sha: repository.active_commit_sha,
      index_version: 4,
      limitation: "Runtime dispatch is intentionally not inferred.",
    },
  ],
  indexed_commit_sha: repository.active_commit_sha,
  active_index_version: 4,
  retrieved_evidence_count: 4,
  tool_call_count: 3,
  duration_ms: 220,
  trace: [
    {
      step: 1,
      tool: "search_code",
      argument_fingerprint: "safe-fingerprint",
      status: "completed",
      duration_ms: 20,
      result_count: 4,
      failure_code: null,
      contributed_evidence: true,
    },
  ],
};

async function fulfillJson(route: Route, json: unknown, status = 200): Promise<void> {
  await route.fulfill({
    contentType: "application/json",
    json,
    status,
  });
}

async function installApiFixture(page: Page, expired = false): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/v1/auth/refresh") {
      if (expired) {
        await fulfillJson(route, { error: { code: "session_expired", message: "Expired" } }, 401);
        return;
      }
      await fulfillJson(route, {
        access_token: "browser-test-token",
        token_type: "bearer",
        expires_in: 900,
      });
      return;
    }
    if (pathname === "/api/v1/auth/me") {
      await fulfillJson(route, {
        id: "44444444-4444-4444-4444-444444444444",
        github_user_id: 1,
        github_login: "octocat",
        display_name: "Octocat",
        avatar_url: null,
        email: null,
      });
      return;
    }
    if (pathname === "/api/v1/auth/logout") {
      await route.fulfill({ status: 204 });
      return;
    }
    if (pathname === "/api/v1/installations") {
      await fulfillJson(route, [
        {
          id: INSTALLATION_ID,
          github_installation_id: 501,
          account_type: "User",
          account_login: "octocat",
          status: "active",
          repository_selection: "selected",
        },
      ]);
      return;
    }
    if (pathname === `/api/v1/installations/${INSTALLATION_ID}/repositories`) {
      await fulfillJson(route, [
        {
          id: "available-1",
          github_repository_id: 9001,
          github_owner: "octocat",
          github_name: repository.github_name,
          github_full_name: repository.github_full_name,
          github_url: repository.github_url,
          is_private: true,
          default_branch: "main",
          primary_language: "Python",
        },
      ]);
      return;
    }
    if (pathname === "/api/v1/repositories" && route.request().method() === "GET") {
      await fulfillJson(route, [repository]);
      return;
    }
    if (pathname === "/api/v1/repositories" && route.request().method() === "POST") {
      await fulfillJson(route, { repository, job: indexingStatus }, 202);
      return;
    }
    if (pathname === `/api/v1/repositories/${REPOSITORY_ID}/status`) {
      await fulfillJson(route, indexingStatus);
      return;
    }
    if (pathname === `/api/v1/repositories/${REPOSITORY_ID}/reindex`) {
      await fulfillJson(
        route,
        { repository: { ...repository, indexing_status: "queued" }, job: indexingStatus },
        202,
      );
      return;
    }
    if (pathname === `/api/v1/repositories/${REPOSITORY_ID}/questions`) {
      await fulfillJson(route, answer);
      return;
    }
    if (pathname === `/api/v1/repositories/${REPOSITORY_ID}`) {
      await fulfillJson(route, repository);
      return;
    }
    await fulfillJson(
      route,
      { error: { code: "not_found", message: "Unexpected fixture request" } },
      404,
    );
  });
}

async function expectNoPageOverflow(page: Page): Promise<void> {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
    .toBe(true);
}

test("sign-in, failed OAuth callback, and expired protected session are understandable", async ({
  page,
}) => {
  await installApiFixture(page, true);
  await page.goto("/signin");
  await expect(
    page.getByRole("heading", { name: "Understand the code you are authorized to see." }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Continue with GitHub" })).toBeVisible();

  await page.goto("/auth/callback");
  await expect(
    page.getByRole("heading", { name: "We could not finish your GitHub session." }),
  ).toBeVisible();
  await expect(page.getByText("No access token was saved in this browser.")).toBeVisible();

  await page.goto(`/repositories/${REPOSITORY_ID}`);
  await expect(page.getByRole("button", { name: "Continue with GitHub" })).toBeVisible();
  await expect(page.getByText("Your session ended.")).toBeVisible();
});

for (const [name, viewport] of Object.entries({
  desktop: { width: 1440, height: 900 },
  laptop: { width: 1024, height: 768 },
  tablet: { width: 768, height: 1024 },
  mobile: { width: 390, height: 844 },
})) {
  test(`repository browser is responsive and keyboard usable at ${name}`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    if (name === "mobile") {
      await page.emulateMedia({ reducedMotion: "reduce" });
    }
    await installApiFixture(page);
    await page.goto("/repositories");
    await expect(
      page.getByRole("heading", { name: "Connect an authorized repository" }),
    ).toBeVisible();
    await expect(
      page.getByRole("table", { name: "Authorized repositories" }).getByText(LONG_REPOSITORY_NAME),
    ).toBeVisible();
    await expect(page.getByText("Repositories are temporarily unavailable.")).toHaveCount(0);
    await expectNoPageOverflow(page);

    const search = page.getByPlaceholder("Search authorized repositories");
    await search.focus();
    await expect(search).toBeFocused();
    await expect(search).toHaveCSS("outline-style", "solid");

    if (name === "mobile") {
      await page.getByRole("button", { name: "Open navigation" }).click();
      await expect(page.getByRole("complementary", { name: "Primary navigation" })).toBeVisible();
      await expect(page.getByRole("complementary", { name: "Primary navigation" })).toHaveCSS(
        "transition-duration",
        /^(?:0\.01ms|1e-05s)$/,
      );
      await page.getByRole("button", { name: "Close navigation", exact: true }).click();
    }
    await page.screenshot({ path: testInfo.outputPath(`repository-${name}.png`), fullPage: true });
  });
}

test("overview reindex dialog traps keyboard focus and restores it after Escape", async ({
  page,
}) => {
  await installApiFixture(page);
  await page.goto(`/repositories/${REPOSITORY_ID}`);
  const reindex = page.getByRole("button", { name: "Reindex" });
  await expect(reindex).toBeVisible();
  await reindex.focus();
  await reindex.press("Enter");
  const confirm = page.getByRole("button", { name: "Queue full reindex" });
  await expect(confirm).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(confirm).toBeHidden();
  await expect(reindex).toBeFocused();
});

test("question workspace renders limited evidence safely and inspects every citation kind", async ({
  page,
}, testInfo) => {
  await installApiFixture(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`/repositories/${REPOSITORY_ID}/workspace`);
  const composer = page.getByPlaceholder("Ask about the active repository index…");
  await composer.fill("Explain the authorization boundary");
  await composer.press("Control+Enter");
  await expect(
    page.getByText(
      "Partially Answered: this response is intentionally limited by available evidence.",
    ),
  ).toBeVisible();
  await expect(page.locator(".markdown-answer img")).toHaveCount(0);
  await expect(page.locator(".markdown-answer a")).toHaveCount(0);

  const codeEvidence = page.getByRole("button", { name: new RegExp(LONG_PATH) });
  await codeEvidence.click();
  await expect(page.getByRole("complementary", { name: "Evidence inspector" })).toContainText(
    "Copy citation",
  );
  await expectNoPageOverflow(page);
  await page.keyboard.press("Escape");
  await expect(codeEvidence).toBeFocused();

  await page.getByRole("button", { name: /aabbccd/ }).click();
  await expect(page.getByRole("link", { name: "Open commit on GitHub" })).toHaveAttribute(
    "href",
    /^https:\/\/github\.com\//,
  );
  await page.getByRole("button", { name: "Close evidence inspector" }).click();

  await page.getByRole("button", { name: "PR #77" }).click();
  await expect(page.getByRole("link", { name: "Open pull request on GitHub" })).toHaveCount(0);
  await page.getByRole("button", { name: "Close evidence inspector" }).click();

  await page.getByRole("button", { name: "indexing.worker.process_delivery" }).click();
  await expect(page.getByText("Runtime dispatch is intentionally not inferred.")).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("workspace-evidence.png"), fullPage: true });
});

test("settings displays server-authorized repository and installation state", async ({ page }) => {
  await installApiFixture(page);
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Account and repository access" })).toBeVisible();
  await expect(page.getByText("octocat", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Manage GitHub App access in GitHub")).toBeVisible();
});
