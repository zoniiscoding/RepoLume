const API_PATH = "/api/v1";

export function validateProductionApiBaseUrl(value: string | undefined): URL {
  if (!value) throw new Error("VITE_API_BASE_URL is required for a production build");
  const url = new URL(value);
  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    url.pathname.replace(/\/$/, "") !== API_PATH
  ) {
    throw new Error("VITE_API_BASE_URL must be an HTTPS URL ending exactly in /api/v1");
  }
  return url;
}

export function createVercelConfig(apiBaseUrl: string | undefined) {
  const apiUrl = validateProductionApiBaseUrl(apiBaseUrl);
  const apiOrigin = apiUrl.origin;
  const contentSecurityPolicy = [
    "default-src 'none'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data: https://avatars.githubusercontent.com https://lh3.googleusercontent.com",
    `connect-src 'self' ${apiOrigin}`,
    "font-src 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "manifest-src 'self'",
    "upgrade-insecure-requests",
  ].join("; ");

  return {
    $schema: "https://openapi.vercel.sh/vercel.json",
    framework: "vite",
    installCommand: "npm ci",
    buildCommand: "npm run build",
    outputDirectory: "dist",
    cleanUrls: true,
    trailingSlash: false,
    headers: [
      {
        source: "/(.*)",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "camera=(), geolocation=(), microphone=(), payment=(), usb=()",
          },
        ],
      },
    ],
    rewrites: [{ source: "/(.*)", destination: "/index.html" }],
  };
}
