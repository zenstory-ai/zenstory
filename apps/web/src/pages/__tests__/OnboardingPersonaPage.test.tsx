import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import zhOnboarding from "../../../public/locales/zh/onboarding.json";
import enOnboarding from "../../../public/locales/en/onboarding.json";
import { ApiError } from "../../lib/apiClient";
import {
  PREFERRED_PROJECT_TYPE_STORAGE_KEY,
  getPreferredProjectType,
  setPreferredProjectType,
} from "../../lib/preferredProjectType";

const mockNavigate = vi.fn();
const mockGetPersonaOnboardingData = vi.fn();
const mockSavePersonaOnboardingData = vi.fn();
const mockGetState = vi.fn();
const mockSave = vi.fn();
const mockToastError = vi.fn();
const { mockDashboardOnboardingFlags } = vi.hoisted(() => ({
  mockDashboardOnboardingFlags: {
    todayActionPlanEnabled: false,
    firstDayActivationGuideEnabled: false,
    coachmarkTourEnabled: false,
  },
}));

vi.mock("../../config/dashboardOnboarding", () => ({
  dashboardOnboardingFlags: mockDashboardOnboardingFlags,
}));

let mockUser: { id: string } | null = { id: "user-onboarding-1" };
let mockLocationState: { from?: { pathname?: string; search?: string; hash?: string } } | null = {
  from: { pathname: "/dashboard/projects", search: "", hash: "" },
};

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
      return key;
    },
  }),
}));

vi.mock("../../contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
  }),
}));

vi.mock("../../lib/onboardingPersona", () => ({
  getPersonaOnboardingData: (...args: unknown[]) => mockGetPersonaOnboardingData(...args),
  savePersonaOnboardingData: (...args: unknown[]) => mockSavePersonaOnboardingData(...args),
}));

vi.mock("../../lib/onboardingPersonaApi", () => ({
  personaOnboardingQueryKey: (userId: string) => ["persona-onboarding", userId],
  onboardingPersonaApi: {
    getState: (...args: unknown[]) => mockGetState(...args),
    save: (...args: unknown[]) => mockSave(...args),
  },
}));

vi.mock("../../lib/toast", () => ({
  toast: { error: (...args: unknown[]) => mockToastError(...args) },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => ({
      pathname: "/onboarding/persona",
      search: "",
      hash: "",
      key: "test",
      state: mockLocationState,
    }),
  };
});

import OnboardingPersonaPage from "../OnboardingPersonaPage";

let queryClient: QueryClient;

const renderPage = () => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <OnboardingPersonaPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

describe("OnboardingPersonaPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser = { id: "user-onboarding-1" };
    mockLocationState = { from: { pathname: "/dashboard/projects", search: "", hash: "" } };
    mockDashboardOnboardingFlags.todayActionPlanEnabled = false;
    mockDashboardOnboardingFlags.firstDayActivationGuideEnabled = false;
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
    mockGetPersonaOnboardingData.mockReturnValue(null);
    mockGetState.mockResolvedValue({ profile: null });
    mockSave.mockImplementation(async (payload) => ({
      required: false,
      profile: {
        ...payload,
        // Mirrors the server default for an unanswered experience question.
        experience_level: payload.experience_level ?? "beginner",
        version: 1,
        completed_at: "2026-03-08T00:00:00.000Z",
      },
    }));
    mockSavePersonaOnboardingData.mockReturnValue({
      version: 1,
      completed_at: "2026-03-08T00:00:00.000Z",
      selected_personas: [],
      selected_goals: [],
      experience_level: "beginner",
      skipped: false,
    });
  });

  it("returns empty render when user is missing", () => {
    mockUser = null;
    const { container } = renderPage();

    expect(container).toBeEmptyDOMElement();
  });

  it("restores existing onboarding data and preselects personas", () => {
    mockGetPersonaOnboardingData.mockReturnValue({
      version: 1,
      completed_at: "2026-03-07T09:00:00.000Z",
      selected_personas: ["explorer"],
      selected_goals: ["monetize"],
      experience_level: "advanced",
      skipped: false,
    });

    renderPage();

    expect(screen.getByText("已带入你上次的选择")).toBeInTheDocument();
    expect(screen.getByText(/1\s*\/\s*3 已选/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /explorer/i })).toHaveAttribute("aria-pressed", "true");
  });

  it("enforces max persona selection limit", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    fireEvent.click(screen.getByRole("button", { name: /serial/i }));
    fireEvent.click(screen.getByRole("button", { name: /professional/i }));
    fireEvent.click(screen.getByRole("button", { name: /fanfic/i }));

    expect(screen.getByText("最多选 3 项，请先取消一个。")).toBeInTheDocument();
    expect(screen.getByText(/3\s*\/\s*3 已选/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /fanfic/i })).toHaveAttribute("aria-pressed", "false");
  });

  it("saves skipped onboarding and redirects to /dashboard when source path is onboarding", async () => {
    mockLocationState = { from: { pathname: "/onboarding/persona" } };

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));

    await waitFor(() => {
      expect(mockSavePersonaOnboardingData).toHaveBeenCalledWith("user-onboarding-1", {
        selected_personas: [],
        selected_goals: [],
        experience_level: "beginner",
        skipped: true,
      });
    });
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard", {
      replace: true,
      state: { startDashboardCoachmark: true },
    });
    expect(queryClient.getQueryData(["persona-onboarding", "user-onboarding-1"])).toMatchObject({
      required: false,
      profile: { skipped: true },
    });
  });

  it("submits selected personas/goals and navigates to source path", async () => {
    renderPage();

    const submitButton = screen.getByRole("button", { name: "保存并进入工作台" });
    expect(submitButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    fireEvent.click(screen.getByRole("button", { name: /monetize/i }));
    fireEvent.click(screen.getByRole("radio", { name: /advanced/i }));
    expect(submitButton).toBeEnabled();

    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockSavePersonaOnboardingData).toHaveBeenCalledWith("user-onboarding-1", {
        selected_personas: ["explorer"],
        selected_goals: ["monetize"],
        experience_level: "advanced",
        skipped: false,
      });
    });
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard/projects", {
      replace: true,
      state: undefined,
    });
    expect(queryClient.getQueryData(["persona-onboarding", "user-onboarding-1"])).toMatchObject({
      required: false,
      profile: { selected_personas: ["explorer"] },
    });
  });

  it("preserves source search/hash when navigating back after submit", async () => {
    mockLocationState = {
      from: {
        pathname: "/project/project-1",
        search: "?file=file-2&tab=outline",
        hash: "#section-1",
      },
    };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    const submitButton = screen.getByRole("button", { name: "保存并进入工作台" });

    await waitFor(() => {
      expect(submitButton).toBeEnabled();
    });

    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        "/project/project-1?file=file-2&tab=outline#section-1",
        { replace: true, state: undefined },
      );
    });
  });

  it("keeps the form retryable without caching or navigating when the API save fails", async () => {
    mockSave.mockRejectedValueOnce(new Error("network"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    const submitButton = screen.getByRole("button", { name: "保存并进入工作台" });
    fireEvent.click(submitButton);

    await waitFor(() => expect(mockToastError).toHaveBeenCalled());
    expect(mockSavePersonaOnboardingData).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(submitButton).toBeEnabled();
    expect(screen.getByRole("button", { name: /explorer/i })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(submitButton);
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(2));
  });

  it("exposes selected state for goal and experience choices", () => {
    renderPage();

    const goal = screen.getByRole("button", { name: /monetize/i });
    expect(goal).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(goal);
    expect(goal).toHaveAttribute("aria-pressed", "true");

    // A first visit does not preselect any experience level.
    for (const level of [/beginner/i, /intermediate/i, /advanced/i]) {
      expect(screen.getByRole("radio", { name: level })).toHaveAttribute("aria-checked", "false");
    }
    fireEvent.click(screen.getByRole("radio", { name: /advanced/i }));
    expect(screen.getByRole("radio", { name: /advanced/i })).toHaveAttribute("aria-checked", "true");
  });

  it("does not send an experience level the author never picked", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave.mock.calls[0][0]).not.toHaveProperty("experience_level");
  });

  it("explains why save is disabled instead of silently ignoring the click", () => {
    renderPage();

    const submitButton = screen.getByRole("button", { name: "保存并进入工作台" });
    expect(submitButton).toBeDisabled();
    const reason = screen.getByTestId("onboarding-submit-reason");
    expect(reason).toHaveTextContent("先选一个创作者类型，或点「跳过」");
    expect(submitButton).toHaveAttribute("aria-describedby", "onboarding-submit-reason");

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    expect(submitButton).toBeEnabled();
    expect(screen.queryByTestId("onboarding-submit-reason")).not.toBeInTheDocument();
  });

  it("saves once when the save button is double-clicked", async () => {
    let resolveSave: (value: unknown) => void = () => {};
    mockSave.mockImplementationOnce(
      (payload) =>
        new Promise((resolve) => {
          resolveSave = () =>
            resolve({
              required: false,
              profile: { ...payload, experience_level: "beginner", version: 1, completed_at: "2026-03-08T00:00:00.000Z" },
            });
        }),
    );
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /explorer/i }));
    const submitButton = screen.getByRole("button", { name: "保存并进入工作台" });
    fireEvent.click(submitButton);
    fireEvent.click(submitButton);

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    resolveSave(undefined);
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledTimes(1);
  });
  it("previews the fanfic, studio and chapter-review recommendations an author picked", () => {
    mockDashboardOnboardingFlags.todayActionPlanEnabled = true;
    renderPage();

    expect(screen.getByText("选一个创作者类型，看看会推荐什么")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /fanfic/i }));
    fireEvent.click(screen.getByRole("button", { name: /studio/i }));
    fireEvent.click(screen.getByRole("button", { name: /improveQuality/i }));
    // The experience tip appears once a level is picked (none is preselected).
    fireEvent.click(screen.getByRole("radio", { name: /intermediate/i }));

    const previewList = screen.getByText("整理角色与设定，AI 写作时参考").closest("ul");
    expect(previewList).not.toBeNull();
    expect(
      within(previewList as HTMLElement).getAllByRole("listitem").map((item) => item.textContent)
    ).toEqual([
      "整理角色与设定，AI 写作时参考",
      "每部作品一个项目，分开管理",
      "让 AI 审读并修改章节",
      "按你的写作经验给出建议",
    ]);
    expect(screen.queryByText("选一个创作者类型，看看会推荐什么")).not.toBeInTheDocument();

    // The fallbacks shown above are the shipped zh copy for these keys.
    expect(zhOnboarding.preview.items).toMatchObject({
      fanfic: "整理角色与设定，AI 写作时参考",
      studio: "每部作品一个项目，分开管理",
      quality: "让 AI 审读并修改章节",
    });

    // Deselecting a persona removes its recommendation from the preview.
    fireEvent.click(screen.getByRole("button", { name: /studio/i }));
    expect(screen.queryByText("每部作品一个项目，分开管理")).not.toBeInTheDocument();
    expect(screen.getByText("整理角色与设定，AI 写作时参考")).toBeInTheDocument();
  });
  it("only promises dashboard recommendations when a dashboard guidance panel is enabled", () => {
    const { unmount } = renderPage();
    expect(screen.queryByText("首页会为你推荐")).not.toBeInTheDocument();
    expect(screen.queryByText("选一个创作者类型，看看会推荐什么")).not.toBeInTheDocument();
    // No empty second column is reserved for the hidden preview.
    expect(document.querySelector('[class*="lg:grid-cols-[1.35fr_0.9fr]"]')).toBeNull();
    unmount();

    mockDashboardOnboardingFlags.firstDayActivationGuideEnabled = true;
    renderPage();
    expect(screen.getByText("首页会为你推荐")).toBeInTheDocument();
  });

  it("offers short-story and screenwriter identities and remembers the matching project type", async () => {
    mockLocationState = { from: { pathname: "/onboarding/persona" } };
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /screenwriter/i }));
    fireEvent.click(screen.getByRole("button", { name: /short_story/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockSave).toHaveBeenCalledTimes(1);
    expect(mockSave.mock.calls[0][0]).toMatchObject({
      selected_personas: ["screenwriter", "short_story"],
      skipped: false,
    });
    // The first matching identity wins.
    expect(getPreferredProjectType()).toBe("screenplay");

    // Shipped copy exists in both locales.
    expect(zhOnboarding.persona.options.short_story.title).toBe("短篇作者");
    expect(zhOnboarding.persona.options.screenwriter.title).toBe("短剧编剧");
    expect(enOnboarding.persona.options.short_story.title).toBe("Short-story writer");
    expect(enOnboarding.persona.options.screenwriter.title).toBe("Short-drama screenwriter");
    expect(Object.keys(enOnboarding.preview.items)).toEqual(Object.keys(zhOnboarding.preview.items));
  });

  it("keeps a fresher landing-page type choice instead of overwriting it", async () => {
    setPreferredProjectType("novel");
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /short_story/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(getPreferredProjectType()).toBe("novel");
  });

  it("does not record a project type for other identities or when skipping", async () => {
    const { unmount } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: /serial/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(1));
    expect(getPreferredProjectType()).toBeNull();
    unmount();

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /short_story/i }));
    fireEvent.click(screen.getByRole("button", { name: "跳过" }));
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(2));
    expect(getPreferredProjectType()).toBeNull();
  });

  it("retries once with only the older identities when an older server rejects the new ids", async () => {
    mockSave.mockRejectedValueOnce(new ApiError(422, "ERR_VALIDATION_ERROR"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /short_story/i }));
    fireEvent.click(screen.getByRole("button", { name: /serial/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(mockSave.mock.calls[0][0].selected_personas).toEqual(["short_story", "serial"]);
    expect(mockSave.mock.calls[1][0]).toMatchObject({ selected_personas: ["serial"], skipped: false });
    expect(mockToastError).not.toHaveBeenCalled();
    expect(getPreferredProjectType()).toBe("short");
  });

  it("falls back to a skipped profile when only new identities were picked and the server is older", async () => {
    mockSave.mockRejectedValueOnce(new ApiError(400, "ERR_VALIDATION_ERROR"));
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /screenwriter/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(mockSave.mock.calls[1][0]).toMatchObject({ selected_personas: [], skipped: true });
    expect(getPreferredProjectType()).toBe("screenplay");
  });

  it("does not retry validation errors when no new identity was sent, and retries at most once", async () => {
    mockSave.mockRejectedValueOnce(new ApiError(422, "ERR_VALIDATION_ERROR"));
    const { unmount } = renderPage();
    fireEvent.click(screen.getByRole("button", { name: /serial/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledTimes(1);
    unmount();

    mockSave.mockReset();
    mockSave.mockRejectedValue(new ApiError(422, "ERR_VALIDATION_ERROR"));
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /short_story/i }));
    fireEvent.click(screen.getByRole("button", { name: "保存并进入工作台" }));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(2));
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(getPreferredProjectType()).toBeNull();
  });
});
