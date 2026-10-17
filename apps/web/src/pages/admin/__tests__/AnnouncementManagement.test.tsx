import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../../lib/apiClient";

const getCurrentAdmin = vi.hoisted(() => vi.fn());
const updateCurrent = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());

vi.mock("../../../lib/announcementApi", () => ({
  announcementApi: { getCurrentAdmin, updateCurrent },
}));
vi.mock("../../../lib/toast", () => ({ toast: { error: toastError, success: toastSuccess } }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));

import AnnouncementManagement from "../AnnouncementManagement";

const saved = {
  title: "旧标题",
  description: "已发布说明",
  action_label: "了解更多",
  action_url: "/docs",
  enabled: true,
  version: 5,
  updated_at: "2026-10-17T00:00:00Z",
  updated_by: "admin-1",
};

function createClient() {
  return new QueryClient({ defaultOptions: { queries: {
    staleTime: 5 * 60_000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    retry: false,
  }, mutations: { retry: false } } });
}

function renderPage(client = createClient()) {
  return render(<QueryClientProvider client={client}><AnnouncementManagement /></QueryClientProvider>);
}

describe("AnnouncementManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCurrentAdmin.mockResolvedValue(saved);
    updateCurrent.mockResolvedValue(saved);
  });

  it("loads the current configuration and saves the edited draft", async () => {
    renderPage();
    const title = await screen.findByLabelText("标题");
    fireEvent.change(title, { target: { value: "新标题" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并展示" }));
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledWith({
      title: "新标题",
      description: "已发布说明",
      action_label: "了解更多",
      action_url: "/docs",
      enabled: true,
      expected_version: 5,
      expected_updated_at: "2026-10-17T00:00:00Z",
    }));
  });

  it("stops the published content without saving an unsubmitted draft", async () => {
    renderPage();
    const title = await screen.findByLabelText("标题");
    fireEvent.change(title, { target: { value: "未保存草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "停止展示" }));
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledWith({
      title: "旧标题",
      description: "已发布说明",
      action_label: "了解更多",
      action_url: "/docs",
      enabled: false,
      expected_version: 5,
      expected_updated_at: "2026-10-17T00:00:00Z",
    }));
    expect(title).toHaveValue("未保存草稿");
  });

  it.each(["/docs/new guide", "https://example.com/new guide", "/docs/new\u00a0guide", "/docs/new\u0085guide", "https://example.com:0/docs"])(
    "does not offer to publish an action URL rejected by the server: %s",
    async (url) => {
      renderPage();
      const link = await screen.findByLabelText("按钮链接（可选）");
      fireEvent.change(link, { target: { value: url } });
      expect(screen.getByRole("button", { name: "保存并展示" })).toBeDisabled();
      expect(updateCurrent).not.toHaveBeenCalled();

      fireEvent.change(link, { target: { value: "/docs/new-guide" } });
      expect(screen.getByRole("button", { name: "保存并展示" })).toBeEnabled();
    },
  );

  it("writes successful saves to the shared query cache", async () => {
    const client = createClient();
    const next = { ...saved, title: "更新后的标题", version: 6, updated_at: "2026-10-17T01:00:00Z" };
    updateCurrent.mockResolvedValue(next);
    renderPage(client);
    fireEvent.change(await screen.findByLabelText("标题"), { target: { value: next.title } });
    fireEvent.click(screen.getByRole("button", { name: "保存并展示" }));
    await waitFor(() => expect(toastSuccess).toHaveBeenCalled());
    expect(client.getQueryData(["admin", "announcement", "current"])).toEqual(next);
  });

  it("waits for fresh configuration when returning with a cached query", async () => {
    const client = createClient();
    const first = renderPage(client);
    await screen.findByLabelText("标题");
    first.unmount();

    let finish!: (value: typeof saved) => void;
    getCurrentAdmin.mockReturnValue(new Promise<typeof saved>(resolve => { finish = resolve; }));
    renderPage(client);
    expect(screen.queryByLabelText("标题")).not.toBeInTheDocument();
    const latest = { ...saved, title: "在其他页面期间更新的配置", version: 6, updated_at: "2026-10-17T01:00:00Z" };
    await act(async () => { finish(latest); });
    const title = await screen.findByLabelText("标题");
    expect(title).toHaveValue(latest.title);
    fireEvent.change(title, { target: { value: "基于最新配置编辑" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并展示" }));
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledWith(expect.objectContaining({
      expected_version: latest.version,
      expected_updated_at: latest.updated_at,
    })));
  });

  it("keeps an unsaved draft when a background query refresh completes", async () => {
    const client = createClient();
    renderPage(client);
    const title = await screen.findByLabelText("标题");
    fireEvent.change(title, { target: { value: "还没保存的草稿" } });
    getCurrentAdmin.mockResolvedValue({ ...saved, title: "服务器新标题", version: 6 });
    await act(async () => { await client.refetchQueries({ queryKey: ["admin", "announcement", "current"] }); });
    expect(title).toHaveValue("还没保存的草稿");
  });

  it("keeps the draft and asks for refresh after a version conflict", async () => {
    updateCurrent.mockRejectedValue(new ApiError(409, "conflict"));
    renderPage();
    const title = await screen.findByLabelText("标题");
    fireEvent.change(title, { target: { value: "我的修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并展示" }));
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("公告已被其他管理员更新，请刷新后重试"));
    expect(title).toHaveValue("我的修改");
    expect(screen.getByRole("button", { name: "加载最新配置" })).toBeInTheDocument();

    getCurrentAdmin.mockResolvedValue({
      ...saved,
      title: "其他管理员的新标题",
      version: 6,
      updated_at: "2026-10-17T01:00:00Z",
    });
    fireEvent.click(screen.getByRole("button", { name: "加载最新配置" }));
    await waitFor(() => expect(title).toHaveValue("其他管理员的新标题"));

    updateCurrent.mockResolvedValue({
      ...saved,
      title: "基于最新配置的修改",
      version: 7,
      updated_at: "2026-10-17T02:00:00Z",
    });
    fireEvent.change(title, { target: { value: "基于最新配置的修改" } });
    fireEvent.click(screen.getByRole("button", { name: "保存并展示" }));
    await waitFor(() => expect(updateCurrent).toHaveBeenLastCalledWith(expect.objectContaining({
      title: "基于最新配置的修改",
      expected_version: 6,
      expected_updated_at: "2026-10-17T01:00:00Z",
    })));
  });
});
