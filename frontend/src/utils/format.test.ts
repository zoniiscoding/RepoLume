import { describe, expect, it } from "vitest";
import { trustedAvatarUrl, trustedGitHubUrl } from "./format";

describe("trusted provider URLs", () => {
  it("allows only canonical GitHub commit and pull-request evidence links", () => {
    const sha = "a".repeat(40);

    expect(trustedGitHubUrl(`https://github.com/repolume/api/commit/${sha}`)).toBe(
      `https://github.com/repolume/api/commit/${sha}`,
    );
    expect(trustedGitHubUrl("https://github.com/repolume/api/pull/42")).toBe(
      "https://github.com/repolume/api/pull/42",
    );
    expect(trustedGitHubUrl("https://github.com.evil.example/repolume/api/pull/42")).toBeNull();
    expect(trustedGitHubUrl("https://github.com/repolume/api/issues/42")).toBeNull();
    expect(trustedGitHubUrl("https://github.com/repolume/api/pull/42?redirect=evil")).toBeNull();
    expect(trustedGitHubUrl("https://user:pass@github.com/repolume/api/pull/42")).toBeNull();
  });

  it("allows only HTTPS avatar URLs from the configured identity providers", () => {
    expect(trustedAvatarUrl("https://avatars.githubusercontent.com/u/1?v=4")).toBe(
      "https://avatars.githubusercontent.com/u/1?v=4",
    );
    expect(trustedAvatarUrl("https://lh3.googleusercontent.com/a/example=s96-c")).toBe(
      "https://lh3.googleusercontent.com/a/example=s96-c",
    );
    expect(trustedAvatarUrl("https://avatars.githubusercontent.com.evil.example/u/1")).toBeNull();
    expect(trustedAvatarUrl("http://avatars.githubusercontent.com/u/1")).toBeNull();
    expect(trustedAvatarUrl("https://user:pass@avatars.githubusercontent.com/u/1")).toBeNull();
  });
});
