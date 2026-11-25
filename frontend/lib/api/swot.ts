import { apiFetch, shouldUseMocks } from "./client";
import type { SwotOverview } from "@/lib/api/types";
import { mockSwot } from "../mocks/data";

export async function getSwotOverview(): Promise<SwotOverview> {
  if (shouldUseMocks()) {
    return mockSwot;
  }
  return apiFetch<SwotOverview>("/swot/overview");
}
