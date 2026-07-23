import { createVercelConfig } from "./deployment-config";

export const config = createVercelConfig(
  process.env.VITE_API_BASE_URL,
  process.env.API_PROXY_TARGET,
);
