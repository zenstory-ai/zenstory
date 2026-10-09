// Use a non-Beijing host timezone to prove quota reset dates still render on
// the Beijing calendar day promised by the product.
process.env.TZ = "UTC";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MaterialsPage from "../MaterialsPage";
import { ApiError } from "../../lib/apiClient";
import { MATERIALS_UPLOAD_MAX_CHARACTERS } from "../../lib/materialUploadValidation";

const mockNavigate = vi.fn();
const mockList = vi.fn();
const mockUpload = vi.fn();
const mockDelete = vi.fn();
const mockRetry = vi.fn();
const mockGetStatus = vi.fn();
const mockGetQuota = vi.fn();
const mockGetCatalog = vi.fn();
const trackEventMock = vi.fn();
const mockToastError = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (
      key: string,
      defaultValueOrOptions?: string | Record<string, unknown>,
      maybeOptions?: Record<string, unknown>
    ) => {
      if (typeof defaultValueOrOptions === "string") {
        if (!maybeOptions) return defaultValueOrOptions;
        let output = defaultValueOrOptions;
        for (const [optionKey, optionValue] of Object.entries(maybeOptions)) {
          output = output.replace(`{{${optionKey}}}`, String(optionValue));
        }
        return output;
      }
      if (
        defaultValueOrOptions &&
        typeof defaultValueOrOptions === "object" &&
        "defaultValue" in defaultValueOrOptions
      ) {
        const defaultValue = defaultValueOrOptions.defaultValue;
        if (typeof defaultValue === "string") {
          let output = defaultValue;
          for (const [optionKey, optionValue] of Object.entries(defaultValueOrOptions)) {
            if (optionKey === "defaultValue") continue;
            output = output.replace(`{{${optionKey}}}`, String(optionValue));
          }
          return output;
        }
      }
      return key;
    },
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("../../hooks/useMediaQuery", () => ({
  useIsMobile: () => false,
  useIsTablet: () => false,
}));

vi.mock("../../lib/materialsApi", () => ({
  materialsApi: {
    list: () => mockList(),
    upload: (...args: unknown[]) => mockUpload(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    retry: (...args: unknown[]) => mockRetry(...args),
  },
}));

vi.mock("../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getStatus: () => mockGetStatus(),
    getQuota: () => mockGetQuota(),
    getCatalog: () => mockGetCatalog(),
  },
  subscriptionQueryKeys: {
    status: () => ["subscription-status", "test-user"],
    quota: () => ["subscription-quota", "test-user"],
  },
}));

vi.mock("../../lib/analytics", () => ({
  trackEvent: (...args: unknown[]) => trackEventMock(...args),
}));

vi.mock("../../lib/toast", () => ({
  toast: {
    error: (...args: unknown[]) => mockToastError(...args),
    success: vi.fn(),
    info: vi.fn(),
  },
}));

vi.mock("../../config/materials", () => ({
  materialsConfig: {
    relationshipsEnabled: false,
  },
}));

vi.mock("../../components/subscription/UpgradePromptModal", () => ({
  UpgradePromptModal: ({ open, title }: { open: boolean; title: string }) =>
    open ? <div data-testid="upgrade-modal">{title}</div> : null,
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

describe("MaterialsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockList.mockResolvedValue([]);
    mockGetCatalog.mockResolvedValue({ tiers: [] });
    mockUpload.mockResolvedValue({
      novel_id: 1,
      title: "test",
      job_id: "job-1",
      status: "pending",
      message: "ok",
    });
    mockDelete.mockResolvedValue(undefined);
    mockRetry.mockResolvedValue({ message: "retry queued" });
    mockGetStatus.mockResolvedValue({
      tier: "pro",
      status: "active",
      display_name: "Pro",
      days_remaining: 30,
      current_period_end: null,
      features: {
        materials_library_access: true,
      },
    });
    mockGetQuota.mockResolvedValue({
      ai_conversations: { used: 0, limit: -1, reset_at: null },
      projects: { used: 0, limit: -1, reset_at: null },
      material_uploads: { used: 0, limit: 5, reset_at: null },
      material_decompositions: { used: 0, limit: 5, reset_at: null },
      skill_creates: { used: 0, limit: 20, reset_at: null },
      inspiration_copies: { used: 0, limit: 10, reset_at: null },
    });
  });

  it("renders teaser state for free users and skips workspace queries", async () => {
    mockGetStatus.mockResolvedValueOnce({
      tier: "free",
      status: "none",
      display_name: "免费版",
      days_remaining: null,
      current_period_end: null,
      features: {
        materials_library_access: false,
      },
    });

    render(<MaterialsPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("上传参考小说，一键拆出章节梗概、角色和世界观")
      ).toBeInTheDocument();
    });

    expect(mockList).not.toHaveBeenCalled();
    expect(trackEventMock).toHaveBeenCalledWith(
      "materials_teaser_exposed",
      expect.objectContaining({ source: "materials_teaser" }),
    );

    // Default decomposition does not produce plotlines; the paywall must not sell them.
    expect(document.body.textContent).not.toContain("剧情线");

    expect(screen.getByRole("button", { name: "查看套餐对比" })).toBeInTheDocument();
  });

  const freeStatus = {
    tier: "free",
    status: "none",
    display_name: "免费版",
    days_remaining: null,
    current_period_end: null,
    features: { materials_library_access: false },
  };
  const freeQuota = (trial: { available: boolean; used: boolean; max_chapters: number }) => ({
    ai_conversations: { used: 0, limit: 10, reset_at: null },
    projects: { used: 0, limit: 3, reset_at: null },
    material_uploads: { used: 0, limit: 0, reset_at: null },
    material_decompositions: { used: 0, limit: 0, reset_at: null },
    skill_creates: { used: 0, limit: 3, reset_at: null },
    inspiration_copies: { used: 0, limit: 10, reset_at: null },
    material_trial: trial,
  });

  it("offers a free author one trial breakdown and explains its limits before upload", async () => {
    mockGetStatus.mockResolvedValue(freeStatus);
    mockGetQuota.mockResolvedValue(freeQuota({ available: true, used: false, max_chapters: 20 }));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    fireEvent.click(await screen.findByTestId("materials-trial-start"));
    expect(await screen.findByTestId("materials-trial-upload-note")).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
  });

  it("keeps one solid call to action when the trial is offered: the header Pro entry steps back", async () => {
    mockGetStatus.mockResolvedValue(freeStatus);
    mockGetQuota.mockResolvedValue(freeQuota({ available: true, used: false, max_chapters: 20 }));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    const trial = await screen.findByTestId("materials-trial-start");
    const headerUpgrade = screen.getByTestId("materials-header-upgrade");
    expect(headerUpgrade).toHaveTextContent("开通 Pro");

    const solid = screen
      .getAllByRole("button")
      .filter((button) => button.className.includes("bg-[hsl(var(--accent-primary))]"));
    expect(solid).toEqual([trial]);
  });

  it("shows a free author their trial book with a note that only the first chapters were broken down", async () => {
    mockGetStatus.mockResolvedValue(freeStatus);
    mockGetQuota.mockResolvedValue(freeQuota({ available: false, used: true, max_chapters: 20 }));
    mockList.mockResolvedValue([
      { id: "n1", title: "参考书", status: "completed", chapters_count: 20, created_at: "2026-10-09T00:00:00Z" },
    ]);

    render(<MaterialsPage />, { wrapper: createWrapper() });

    expect(await screen.findByTestId("materials-trial-banner")).toBeInTheDocument();
    expect(await screen.findByText("参考书")).toBeInTheDocument();
    expect(screen.queryByTestId("materials-trial-start")).not.toBeInTheDocument();
  });

  it("states the Pro breakdown limit from the plan catalog instead of a constant", async () => {
    mockGetStatus.mockResolvedValueOnce({
      tier: "free",
      status: "none",
      display_name: "免费版",
      days_remaining: null,
      current_period_end: null,
      features: { materials_library_access: false },
    });
    mockGetCatalog.mockResolvedValueOnce({
      version: "test",
      comparison_mode: "pro_only",
      pricing_anchor_monthly_cents: 4900,
      tiers: [
        { name: "free", entitlements: { material_decompositions_monthly: 0 } },
        { name: "pro", entitlements: { material_decompositions_monthly: 8 } },
      ],
    });

    render(<MaterialsPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("开通 Pro 后，每月可拆解 8 次。")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("每月可拆解 5 次");
  });

  it("omits the breakdown number when the plan catalog is unavailable", async () => {
    mockGetStatus.mockResolvedValueOnce({
      tier: "free",
      status: "none",
      display_name: "免费版",
      days_remaining: null,
      current_period_end: null,
      features: { materials_library_access: false },
    });
    mockGetCatalog.mockRejectedValueOnce(new Error("catalog down"));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("开通 Pro 后，每月都能拆解参考小说。")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/每月可拆解 \d+ 次/);
  });

  it("does not fetch the plan catalog for users who already have the library", async () => {
    render(<MaterialsPage />, { wrapper: createWrapper() });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    expect(mockGetCatalog).not.toHaveBeenCalled();
  });

  it("allows retrying a material with partial decomposition errors", async () => {
    mockList.mockResolvedValueOnce([{
      id: "partial-novel", title: "Partial Novel", status: "completed_with_errors",
      chapters_count: 3, error_message: "One chapter failed",
    }]);
    render(<MaterialsPage />, { wrapper: createWrapper() });
    await screen.findByText("Partial Novel");
    // Raw (legacy) error text never reaches the card; it is mapped to a code.
    expect(screen.queryByText("One chapter failed")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mockRetry).toHaveBeenCalledWith("partial-novel"));
  });

  it("shows exhausted state for paid users without upgrade modal", async () => {
    mockList.mockResolvedValueOnce([
      {
        id: "novel-1",
        title: "Broken Novel",
        original_filename: "broken.txt",
        status: "failed",
        chapters_count: 0,
        error_message: "failed once",
      },
    ]);
    mockGetQuota.mockResolvedValueOnce({
      ai_conversations: { used: 0, limit: -1, reset_at: null },
      projects: { used: 0, limit: -1, reset_at: null },
      material_uploads: { used: 0, limit: 5, reset_at: null },
      material_decompositions: {
        used: 5,
        limit: 5,
        reset_at: "2026-04-30T16:00:00Z",
      },
      skill_creates: { used: 0, limit: 20, reset_at: null },
      inspiration_copies: { used: 0, limit: 10, reset_at: null },
    });

    render(<MaterialsPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("本月 5 次拆解已用完，将于 2026/05/01 恢复。已拆好的内容仍可查看和引用。")
      ).toBeInTheDocument();
    });

    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("upgrade-modal")).not.toBeInTheDocument();
  });

  it("shows error state when subscription status fails instead of teaser", async () => {
    mockGetStatus.mockRejectedValueOnce(new Error("subscription failed"));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    await waitFor(() => {
      expect(
        screen.getByText("没能确认你的 Pro 状态，请重试。")
      ).toBeInTheDocument();
    });

    expect(
      screen.queryByText("上传参考小说，一键拆出章节梗概、角色和世界观")
    ).not.toBeInTheDocument();
    expect(trackEventMock).not.toHaveBeenCalledWith(
      "materials_teaser_exposed",
      expect.anything(),
    );
  });

  it("shows a retryable error when the materials list fails", async () => {
    mockList.mockRejectedValueOnce(new Error("materials offline")).mockResolvedValueOnce([]);
    render(<MaterialsPage />, { wrapper: createWrapper() });

    expect(await screen.findByText("素材列表加载失败，请重试。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2));
  });

  it("blocks every upload modal close path while upload is pending", async () => {
    let resolveUpload!: () => void;
    mockUpload.mockReturnValueOnce(new Promise<void>((resolve) => { resolveUpload = resolve; }));
    render(<MaterialsPage />, { wrapper: createWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: "materials:upload" }));
    fireEvent.change(document.querySelector('input[type="file"]') as HTMLInputElement, {
      target: { files: [new File(["valid"], "pending.txt", { type: "text/plain" })] },
    });
    fireEvent.click(await screen.findByRole("button", { name: "materials:uploadModal.upload" }));
    expect(screen.getByRole("button", { name: "common:cancel" })).toBeDisabled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByText("pending.txt")).toBeInTheDocument();
    resolveUpload();
  });

  it("supports keyboard navigation for opening material detail cards", async () => {
    mockList.mockResolvedValueOnce([
      {
        id: "novel-2",
        title: "Keyboard Novel",
        original_filename: "keyboard.txt",
        status: "completed",
        chapters_count: 12,
        error_message: null,
      },
    ]);

    render(<MaterialsPage />, { wrapper: createWrapper() });

    const detailEntry = await screen.findByRole("button", {
      name: /Keyboard Novel/,
    });

    fireEvent.keyDown(detailEntry, { key: "Enter" });
    fireEvent.keyDown(detailEntry, { key: " " });

    expect(mockNavigate).toHaveBeenNthCalledWith(1, "/materials/novel-2");
    expect(mockNavigate).toHaveBeenNthCalledWith(2, "/materials/novel-2");
  });


  it("surfaces material delete failures to the user", async () => {
    mockList.mockResolvedValueOnce([
      {
        id: "novel-delete-fail",
        title: "Delete Fails",
        original_filename: "delete-fails.txt",
        status: "completed",
        chapters_count: 3,
        error_message: null,
      },
    ]);
    mockDelete.mockRejectedValueOnce(new ApiError(500, "delete failed"));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    await screen.findByRole("button", { name: /Delete Fails/ });
    const deleteButton = document.querySelector("button.absolute") as HTMLButtonElement;
    expect(deleteButton).toBeTruthy();

    fireEvent.click(deleteButton);
    fireEvent.click(screen.getByRole("button", { name: "common:delete" }));

    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledWith("novel-delete-fail");
      expect(mockToastError).toHaveBeenCalledWith("delete failed");
    });
  });

  it("shows an inline error before upload when the selected file exceeds 300k characters", async () => {
    render(<MaterialsPage />, { wrapper: createWrapper() });

    const openUploadButton = await screen.findByRole("button", {
      name: "materials:upload",
    });
    fireEvent.click(openUploadButton);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(fileInput).toBeTruthy();

    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(
            ["字".repeat(MATERIALS_UPLOAD_MAX_CHARACTERS + 1)],
            "too-long.txt",
            { type: "text/plain" },
          ),
        ],
      },
    });

    await waitFor(() => {
      expect(
        screen.getByText("materials:uploadModal.errors.tooManyCharacters")
      ).toBeInTheDocument();
    });

    expect(mockUpload).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "materials:uploadModal.upload" })
    ).toBeDisabled();
  });

  it("shows materials-specific copy when backend rejects the upload as over-limit", async () => {
    mockUpload.mockRejectedValueOnce(new ApiError(400, "ERR_FILE_CONTENT_TOO_LONG"));

    render(<MaterialsPage />, { wrapper: createWrapper() });

    const openUploadButton = await screen.findByRole("button", {
      name: "materials:upload",
    });
    fireEvent.click(openUploadButton);

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(fileInput, {
      target: {
        files: [new File(["valid content"], "valid.txt", { type: "text/plain" })],
      },
    });

    await waitFor(() => {
      expect(screen.getByText("valid.txt")).toBeInTheDocument();
    });

    fireEvent.click(
      screen.getByRole("button", { name: "materials:uploadModal.upload" })
    );

    await waitFor(() => {
      expect(
        screen.getByText("materials:uploadModal.errors.tooManyCharacters")
      ).toBeInTheDocument();
    });
  });
});
