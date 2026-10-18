import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it.each(["success", "error", "conflict"])("protects the draft during a pending save and unlocks after %s", async (outcome) => {
    const user = userEvent.setup();
    let resolveSave!: (value: typeof saved) => void;
    let rejectSave!: (error: Error) => void;
    updateCurrent.mockReturnValue(new Promise<typeof saved>((resolve, reject) => {
      resolveSave = resolve;
      rejectSave = reject;
    }));
    renderPage();
    const title = await screen.findByLabelText("标题");
    await act(async () => { await user.clear(title); });
    await act(async () => { await user.type(title, "提交的标题"); });
    await act(async () => { await user.click(screen.getByRole("button", { name: "保存并展示" })); });
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledTimes(1));

    await act(async () => { await user.type(title, "保存期间追加"); });
    expect(title).toHaveValue("提交的标题");
    for (const field of screen.getAllByRole("textbox")) expect(field).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存中…" })).toBeDisabled();
    const stop = screen.getByRole("button", { name: "停止展示" });
    expect(stop).toBeDisabled();
    await act(async () => { await user.click(stop); });
    expect(updateCurrent).toHaveBeenCalledTimes(1);

    await act(async () => {
      if (outcome === "success") resolveSave({ ...saved, title: "提交的标题", version: 6 });
      else rejectSave(outcome === "conflict" ? new ApiError(409, "conflict") : new Error("offline"));
    });
    await waitFor(() => {
      for (const field of screen.getAllByRole("textbox")) expect(field).toBeEnabled();
    });
    expect(title).toHaveValue("提交的标题");
    expect(screen.getByRole("button", { name: "保存并展示" })).toBeEnabled();
    expect(stop).toBeEnabled();
    await act(async () => { await user.type(title, "完成后继续"); });
    expect(title).toHaveValue("提交的标题完成后继续");
    if (outcome === "conflict") expect(screen.getByRole("button", { name: "加载最新配置" })).toBeEnabled();
  });

  it("protects the unsaved draft while stopping the published announcement", async () => {
    const user = userEvent.setup();
    let finishStop!: (value: typeof saved) => void;
    updateCurrent.mockReturnValue(new Promise<typeof saved>(resolve => { finishStop = resolve; }));
    renderPage();
    const title = await screen.findByLabelText("标题");
    await act(async () => { await user.clear(title); });
    await act(async () => { await user.type(title, "尚未保存的标题"); });
    await act(async () => { await user.click(screen.getByRole("button", { name: "停止展示" })); });
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledTimes(1));
    for (const field of screen.getAllByRole("textbox")) expect(field).toBeDisabled();
    await act(async () => { await user.type(title, "停止期间追加"); });
    expect(title).toHaveValue("尚未保存的标题");
    await act(async () => { await user.click(screen.getByRole("button", { name: "保存中…" })); });
    expect(updateCurrent).toHaveBeenCalledTimes(1);
    await act(async () => { finishStop({ ...saved, enabled: false }); });
    await waitFor(() => expect(title).toBeEnabled());
    expect(title).toHaveValue("尚未保存的标题");
    expect(screen.getByRole("button", { name: "保存并展示" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止展示" })).toBeDisabled();
  });

  it.each(["success", "error"])("serializes explicit reload with editing and mutations, then unlocks after %s", async (outcome) => {
    const user = userEvent.setup();
    updateCurrent.mockRejectedValueOnce(new ApiError(409, "conflict"));
    renderPage();
    const title = await screen.findByLabelText("标题");
    await act(async () => { await user.clear(title); });
    await act(async () => { await user.type(title, "冲突后保留的标题"); });
    await act(async () => { await user.click(screen.getByRole("button", { name: "保存并展示" })); });
    const reload = await screen.findByRole("button", { name: "加载最新配置" });
    let resolveReload!: (value: typeof saved) => void;
    let rejectReload!: (error: Error) => void;
    getCurrentAdmin.mockReturnValue(new Promise<typeof saved>((resolve, reject) => {
      resolveReload = resolve;
      rejectReload = reject;
    }));
    await act(async () => { await user.click(reload); });
    await waitFor(() => expect(getCurrentAdmin).toHaveBeenCalledTimes(2));
    for (const field of screen.getAllByRole("textbox")) expect(field).toBeDisabled();
    await act(async () => { await user.type(title, "读取期间追加"); });
    expect(title).toHaveValue("冲突后保留的标题");
    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
      await act(async () => { await user.click(button); });
    }
    expect(updateCurrent).toHaveBeenCalledTimes(1);
    expect(getCurrentAdmin).toHaveBeenCalledTimes(2);
    await act(async () => {
      if (outcome === "success") resolveReload({ ...saved, title: "服务器最新标题", version: 6 });
      else rejectReload(new Error("offline"));
    });
    await waitFor(() => {
      for (const field of screen.getAllByRole("textbox")) expect(field).toBeEnabled();
    });
    expect(title).toHaveValue(outcome === "success" ? "服务器最新标题" : "冲突后保留的标题");
    expect(screen.getByRole("button", { name: "保存并展示" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "停止展示" })).toBeEnabled();
    if (outcome === "error") expect(reload).toBeEnabled();
    else expect(reload).not.toBeInTheDocument();
  });

  it("blocks explicit reload while retrying a conflicted save", async () => {
    const user = userEvent.setup();
    updateCurrent.mockRejectedValueOnce(new ApiError(409, "conflict"));
    renderPage();
    const title = await screen.findByLabelText("标题");
    await act(async () => { await user.click(screen.getByRole("button", { name: "保存并展示" })); });
    const reload = await screen.findByRole("button", { name: "加载最新配置" });
    let finishSave!: (value: typeof saved) => void;
    updateCurrent.mockReturnValue(new Promise<typeof saved>(resolve => { finishSave = resolve; }));
    await act(async () => { await user.click(screen.getByRole("button", { name: "保存并展示" })); });
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledTimes(2));
    expect(reload).toBeDisabled();
    await act(async () => { await user.click(reload); });
    expect(getCurrentAdmin).toHaveBeenCalledTimes(1);
    await act(async () => { finishSave({ ...saved, version: 6 }); });
    await waitFor(() => expect(title).toBeEnabled());
    expect(reload).not.toBeInTheDocument();
  });

  it.each([
    "/docs/new guide",
    "https://example.com/new guide",
    "/docs/new\u00a0guide",
    "/docs/new\u0085guide",
    "https://example.com:0/docs",
    "https:example.com/docs",
    "https:/example.com/docs",
    "https:///example.com/docs",
    "https://@example.com/docs",
    "https://:@example.com/docs",
  ])(
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

  it.each([
    "/docs?source=announcement#intro",
    "https://example.com:8443/docs?source=announcement#intro",
    "HTTPS://example.com/docs",
    "https://[::1]:8443/docs",
  ])("preserves valid action URLs accepted by the server: %s", async (url) => {
    renderPage();
    const link = await screen.findByLabelText("按钮链接（可选）");
    fireEvent.change(link, { target: { value: url } });
    const save = screen.getByRole("button", { name: "保存并展示" });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(updateCurrent).toHaveBeenCalledWith(expect.objectContaining({ action_url: url })));
  });

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
