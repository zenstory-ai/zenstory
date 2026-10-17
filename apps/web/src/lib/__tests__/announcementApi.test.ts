import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
const put = vi.hoisted(() => vi.fn());
vi.mock("../apiClient", () => ({ api: { get, put } }));

import { announcementApi } from "../announcementApi";

describe("announcementApi", () => {
  beforeEach(() => vi.clearAllMocks());

  it("uses the public and admin announcement routes", async () => {
    get.mockResolvedValueOnce(null).mockResolvedValueOnce({ version: 0 });
    await announcementApi.getCurrent();
    await announcementApi.getCurrentAdmin();
    expect(get).toHaveBeenNthCalledWith(1, "/api/announcements/current");
    expect(get).toHaveBeenNthCalledWith(2, "/api/admin/announcements/current");
  });

  it("sends the complete optimistic-concurrency update", async () => {
    const request = {
      title: "更新",
      description: "说明",
      action_label: null,
      action_url: null,
      enabled: true,
      expected_version: 4,
      expected_updated_at: "2026-10-17T00:00:00Z",
    };
    put.mockResolvedValue({ ...request, version: 5 });
    await announcementApi.updateCurrent(request);
    expect(put).toHaveBeenCalledWith("/api/admin/announcements/current", request);
  });
});
