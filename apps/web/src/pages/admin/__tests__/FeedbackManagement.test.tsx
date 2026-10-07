import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";

import FeedbackManagement from "../FeedbackManagement";
import { toast } from "../../../lib/toast";
import { MemoryRouter } from "react-router-dom";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();
const getScreenshotBlobMock = vi.fn();
// When on, t() returns the component's inline fallback copy, so tests can check the wording users see.
const i18nState = vi.hoisted(() => ({ useFallback: false }));

vi.mock("../../../lib/adminApi", () => ({
  adminApi: {
    getFeedbackList: vi.fn(),
    updateFeedbackStatus: vi.fn(),
    getFeedbackScreenshotBlob: (...args: unknown[]) => getScreenshotBlobMock(...args),
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown) =>
      i18nState.useFallback && typeof fallback === "string" ? fallback : key,
  }),
}));

vi.mock("../../../lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

vi.mock("../../../hooks/useMediaQuery", () => ({
  useIsMobile: () => false,
  useMediaQuery: () => false,
}));

describe("FeedbackManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18nState.useFallback = false;
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
  });

  it("shows loading state", () => {
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<FeedbackManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load feedback failed"),
      refetch: refetchMock,
    });

    render(<FeedbackManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("load feedback failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<FeedbackManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("feedback.empty")).toBeInTheDocument();
  });

  it("renders table rows and updates feedback status", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            id: "fb-1",
            user_id: "user-1",
            username: "feedback_user",
            email: "feedback_user@example.com",
            source_page: "editor",
            source_route: "/project/test",
            issue_text: "Toolbar overlaps on mobile",
            has_screenshot: true,
            screenshot_original_name: "bug.png",
            screenshot_content_type: "image/png",
            screenshot_size_bytes: 1024,
            screenshot_download_url: "/api/admin/feedback/fb-1/screenshot",
            status: "open",
            created_at: "2026-03-08T00:00:00Z",
            updated_at: "2026-03-08T00:00:00Z",
          },
        ],
        total: 1,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<FeedbackManagement />, { wrapper: MemoryRouter });

    const issueText = screen.getByText("Toolbar overlaps on mobile");
    expect(issueText).toBeInTheDocument();
    expect(screen.getByText("feedback.viewScreenshot")).toBeInTheDocument();

    const row = issueText.closest("tr");
    expect(row).not.toBeNull();
    const statusSelect = within(row as HTMLTableRowElement).getByRole("combobox");
    fireEvent.change(statusSelect, { target: { value: "resolved" } });

    expect(mutateMock).toHaveBeenCalledWith({ id: "fb-1", status: "resolved" });
  });

  it("does not create or commit a screenshot URL after the preview closes", async () => {
    let resolveBlob!: (value: Blob) => void;
    getScreenshotBlobMock.mockReturnValueOnce(new Promise((resolve) => { resolveBlob = resolve; }));
    const createObjectURL = vi.fn(() => "blob:late");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    useQueryMock.mockReturnValue({
      data: { items: [{
        id: "fb-late", user_id: "user-1", username: "late_user", email: "late@example.com",
        source_page: "editor", source_route: "/project/test", issue_text: "Late screenshot",
        has_screenshot: true, screenshot_original_name: "late.png", screenshot_content_type: "image/png",
        screenshot_size_bytes: 10, status: "open", created_at: "2026-03-08T00:00:00Z",
        updated_at: "2026-03-08T00:00:00Z",
      }], total: 1 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });
    render(<FeedbackManagement />, { wrapper: MemoryRouter });
    fireEvent.click(screen.getByText("feedback.viewScreenshot"));
    fireEvent.click(screen.getByText("common:close"));
    await act(async () => { resolveBlob(new Blob(["image"])); await Promise.resolve(); });
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByAltText("feedback.screenshotPreviewAlt")).not.toBeInTheDocument();
  });

  describe("status update failure", () => {
    type StatusMutationOptions = { onError: (err: unknown) => void };
    const renderAndGetStatusMutation = () => {
      i18nState.useFallback = true;
      useQueryMock.mockReturnValue({
        data: { items: [], total: 0 },
        isLoading: false,
        isFetching: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      });
      render(<FeedbackManagement />, { wrapper: MemoryRouter });
      const options = useMutationMock.mock.calls.at(-1)?.[0] as StatusMutationOptions | undefined;
      expect(options).toBeDefined();
      return options as StatusMutationOptions;
    };

    it("shows the server's message when the request fails with an Error", () => {
      const { onError } = renderAndGetStatusMutation();

      act(() => onError(new Error("反馈不存在")));

      expect(toast.error).toHaveBeenCalledWith("反馈不存在");
      expect(toast.success).not.toHaveBeenCalled();
    });

    it("falls back to the retry hint when the failure carries no message", () => {
      const { onError } = renderAndGetStatusMutation();

      act(() => onError({ status: 500 }));

      expect(toast.error).toHaveBeenCalledWith("状态更新失败，请重试");
      expect(toast.success).not.toHaveBeenCalled();
    });
  });
});
