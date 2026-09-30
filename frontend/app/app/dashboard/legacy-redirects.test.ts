import { describe, expect, it, vi } from "vitest";

const { redirect } = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect }));

import AnalyticsPage from "../analytics/page";
import DeliverabilityPage from "../deliverability/page";

describe("legacy Analytics and Deliverability routes", () => {
  it("send old bookmarks to the merged dashboard", () => {
    AnalyticsPage();
    DeliverabilityPage();

    expect(redirect).toHaveBeenCalledTimes(2);
    expect(redirect).toHaveBeenNthCalledWith(1, "/app/dashboard");
    expect(redirect).toHaveBeenNthCalledWith(2, "/app/dashboard");
  });
});
