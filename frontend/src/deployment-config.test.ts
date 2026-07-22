import { describe, expect, it } from "vitest";
import { createVercelConfig, validateProductionApiBaseUrl } from "../deployment-config";

describe("production deployment configuration", () => {
  it("binds browser connections and SPA routing to the exact production API", () => {
    const configuration = createVercelConfig("https://api.repolume.example/api/v1");
    const csp = configuration.headers[0]?.headers.find(
      (header) => header.key === "Content-Security-Policy",
    );

    expect(csp?.value).toContain("connect-src 'self' https://api.repolume.example");
    expect(csp?.value).toContain("frame-ancestors 'none'");
    expect(configuration.rewrites).toEqual([{ source: "/(.*)", destination: "/index.html" }]);
  });

  it.each([
    undefined,
    "http://api.repolume.example/api/v1",
    "https://user:password@api.repolume.example/api/v1",
    "https://api.repolume.example/api/v1?forward=unsafe",
    "https://api.repolume.example/api/v10",
  ])("rejects an unsafe production API base: %s", (value) => {
    expect(() => validateProductionApiBaseUrl(value)).toThrow();
  });
});
