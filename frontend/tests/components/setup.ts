import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Unmount rendered components and reset fakes between tests.
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
