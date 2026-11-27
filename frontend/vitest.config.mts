import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Component tests only. The plain logic tests in tests/*.test.mjs run on node:test.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    include: ["tests/components/**/*.test.tsx"],
    setupFiles: ["./tests/components/setup.ts"],
  },
});
