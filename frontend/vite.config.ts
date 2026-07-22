import { configDefaults, defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { validateProductionApiBaseUrl } from "./deployment-config";

export default defineConfig(({ command, mode }) => {
  if (command === "build") {
    const environment = loadEnv(mode, process.cwd(), "");
    validateProductionApiBaseUrl(environment.VITE_API_BASE_URL);
  }
  return {
    plugins: [react()],
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      exclude: [...configDefaults.exclude, "e2e/**"],
      css: true,
      coverage: {
        reporter: ["text", "html"],
        exclude: ["src/test/**", "src/main.tsx"],
      },
    },
  };
});
