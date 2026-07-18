import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiProtocolError } from "./client";

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses cookie credentials and an in-memory bearer token for protected calls", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify([]), { headers: { "content-type": "application/json" } }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await api.listRepositories("memory-only-token");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/repositories$/),
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({ Authorization: "Bearer memory-only-token" }),
      }),
    );
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Origin");
  });

  it("normalizes safe API failures without exposing response internals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "not_found",
              message: "Repository was not found",
              request_id: "safe-id",
            },
          }),
          { status: 404, headers: { "content-type": "application/json" } },
        ),
      ),
    );

    await expect(api.listRepositories("token")).rejects.toEqual(
      expect.objectContaining({ status: 404, code: "not_found", requestId: "safe-id" }),
    );
  });

  it("rejects a malformed successful response without rendering server internals", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response("not json", { headers: { "content-type": "text/plain" } })),
    );

    await expect(api.listRepositories("token")).rejects.toBeInstanceOf(ApiProtocolError);
  });
});
