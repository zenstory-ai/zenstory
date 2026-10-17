import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getCurrent = vi.hoisted(() => vi.fn());

vi.mock("../../../lib/announcementApi", () => ({
  announcementApi: { getCurrent },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));

import { AnnouncementBanner } from "../AnnouncementBanner";

const announcement = {
  title: "功能更新",
  description: "现在可以更专注地继续创作。",
  action_label: "查看详情",
  action_url: "/docs/updates",
  version: 3,
  updated_at: "2026-10-17T00:00:00Z",
};

function createClient() {
  return new QueryClient({ defaultOptions: { queries: {
    staleTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  } } });
}

function renderBanner(accountId = "user-1", client = createClient()) {
  return render(
    <QueryClientProvider client={client}>
      <AnnouncementBanner accountId={accountId} />
    </QueryClientProvider>,
  );
}

describe("AnnouncementBanner", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    getCurrent.mockResolvedValue(announcement);
  });

  it("does not block or show an error when loading fails", async () => {
    getCurrent.mockRejectedValue(new Error("offline"));
    renderBanner();
    await waitFor(() => expect(getCurrent).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("region", { name: "站内公告" })).not.toBeInTheDocument();
  });

  it.each([null, announcement])("refreshes invalidated cached content on SPA return: %s", async (cached) => {
    const client = createClient();
    client.setQueryData(["announcement", "current", "user-1"], cached);
    await client.invalidateQueries({ queryKey: ["announcement", "current"] });
    getCurrent.mockResolvedValue({ ...announcement, title: "刚发布的新公告", version: 4 });
    renderBanner("user-1", client);
    expect(await screen.findByText("刚发布的新公告")).toBeVisible();
    expect(getCurrent).toHaveBeenCalledTimes(1);
  });

  it("removes a stopped banner after returning to an invalidated cached query", async () => {
    const client = createClient();
    client.setQueryData(["announcement", "current", "user-1"], announcement);
    await client.invalidateQueries({ queryKey: ["announcement", "current"] });
    getCurrent.mockResolvedValue(null);
    renderBanner("user-1", client);
    await waitFor(() => expect(getCurrent).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("region", { name: "站内公告" })).not.toBeInTheDocument());
  });

  it("remembers dismissal for the same account and version", async () => {
    const first = renderBanner();
    expect(await screen.findByText("功能更新")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭公告" }));
    expect(screen.queryByText("功能更新")).not.toBeInTheDocument();
    first.unmount();

    renderBanner();
    await waitFor(() => expect(getCurrent).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("功能更新")).not.toBeInTheDocument();
  });

  it("scopes dismissal by account and shows a new version again", async () => {
    const first = renderBanner("user-a");
    await screen.findByText("功能更新");
    fireEvent.click(screen.getByRole("button", { name: "关闭公告" }));
    first.unmount();

    const second = renderBanner("user-b");
    expect(await screen.findByText("功能更新")).toBeInTheDocument();
    second.unmount();

    getCurrent.mockResolvedValue({ ...announcement, version: 4 });
    renderBanner("user-a");
    expect(await screen.findByText("功能更新")).toBeInTheDocument();
  });

  it("still dismisses for the current session when storage fails", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    renderBanner();
    await screen.findByText("功能更新");
    fireEvent.click(screen.getByRole("button", { name: "关闭公告" }));
    expect(screen.queryByText("功能更新")).not.toBeInTheDocument();
    setItem.mockRestore();
  });

  it("opens only safe actions in a new tab", async () => {
    const view = renderBanner();
    const link = await screen.findByRole("link", { name: "查看详情" });
    expect(link).toHaveAttribute("href", "/docs/updates");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    view.unmount();

    getCurrent.mockResolvedValue({ ...announcement, action_url: "javascript:alert(1)" });
    renderBanner("user-2");
    await screen.findByText("功能更新");
    expect(screen.queryByRole("link", { name: "查看详情" })).not.toBeInTheDocument();
  });
});
