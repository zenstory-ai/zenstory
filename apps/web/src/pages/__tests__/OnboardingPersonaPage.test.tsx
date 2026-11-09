import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const mockInitializeFirstWork = vi.fn();
const mockRequestChatOnOpen = vi.fn();
let mockIsMobile = false;
let mockUser: { id: string } | null = { id: "user-onboarding-1" };
let mockLocationState: { from?: { pathname?: string; search?: string; hash?: string } } | null = {
  from: { pathname: "/dashboard/projects", search: "", hash: "" },
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    i18n: { language: "zh" },
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

const mockReportActivationEvent = vi.fn();
vi.mock("../../lib/activationEvents", () => ({
  reportActivationEvent: (...args: unknown[]) => mockReportActivationEvent(...args),
}));

vi.mock("../../lib/firstWorkApi", () => ({
  firstWorkQueryKey: (userId: string | null) => ["workspace-first-work", userId],
  firstWorkApi: { initialize: (...args: unknown[]) => mockInitializeFirstWork(...args) },
}));

vi.mock("../../hooks/useMediaQuery", () => ({ useIsMobile: () => mockIsMobile }));

vi.mock("../../lib/pendingInspiration", () => ({
  requestChatOnOpen: (...args: unknown[]) => mockRequestChatOnOpen(...args),
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

const firstWorkState = (overrides: Record<string, unknown> = {}) => ({
  status: "created",
  project_id: "first-project",
  project_type: "short",
  file_id: "first-file",
  available: true,
  static_guidance: true,
  opened_at: null,
  first_manual_write_at: null,
  first_ai_requested_at: null,
  ...overrides,
});

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
    mockIsMobile = false;
    mockInitializeFirstWork.mockResolvedValue(firstWorkState({ status: "ineligible", project_id: null, file_id: null, available: false, static_guidance: false }));
    localStorage.removeItem(PREFERRED_PROJECT_TYPE_STORAGE_KEY);
    localStorage.removeItem(`${PREFERRED_PROJECT_TYPE_STORAGE_KEY}:${mockUser.id}`);
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

  const typeButton = (type: "novel" | "short" | "screenplay") =>
    screen.getByTestId(`onboarding-type-${type}`);

  it("returns empty render when user is missing", () => {
    mockUser = null;
    const { container } = renderPage();

    expect(container).toBeEmptyDOMElement();
  });

  it("asks one question and goes on with one tap", async () => {
    renderPage();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("你准备写哪种作品？");
    fireEvent.click(typeButton("short"));

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave.mock.calls[0][0]).toEqual({ selected_personas: ["short_story"], selected_goals: [], skipped: false });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/dashboard/projects", { replace: true });
    });
    expect(getPreferredProjectType(mockUser?.id)).toBe("short");
    expect(mockReportActivationEvent).toHaveBeenCalledWith("user-onboarding-1", { event_name: "persona_submitted", skipped: false });
    expect(queryClient.getQueryData(["persona-onboarding", "user-onboarding-1"])).toMatchObject({
      required: false,
      profile: { selected_personas: ["short_story"] },
    });

    // Shipped copy exists in both locales for every option.
    for (const type of ["novel", "short", "screenplay"] as const) {
      expect(zhOnboarding.start.options[type].title).toBeTruthy();
      expect(enOnboarding.start.options[type].title).toBeTruthy();
      // Each locale lists the platforms, the folders the work opens with and a sample opening.
      for (const options of [zhOnboarding.start.options[type], enOnboarding.start.options[type]]) {
        expect(options.platforms.length).toBeGreaterThan(0);
        expect(options.folders.length).toBeGreaterThan(0);
        expect(options.sample.length).toBeGreaterThan(1);
      }
    }
    // Only the default destination opens the first work.
    expect(mockInitializeFirstWork).not.toHaveBeenCalled();
  });

  it("keeps earlier identities, goals and experience it no longer asks about", async () => {
    mockGetPersonaOnboardingData.mockReturnValue({
      version: 1,
      completed_at: "2026-03-07T09:00:00.000Z",
      selected_personas: ["explorer", "serial", "fanfic"],
      selected_goals: ["monetize"],
      experience_level: "advanced",
      skipped: false,
    });
    renderPage();

    expect(screen.getByText("已带入你上次的选择")).toBeInTheDocument();
    expect(typeButton("novel")).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(typeButton("screenplay"));

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave.mock.calls[0][0]).toEqual({
      selected_personas: ["screenwriter", "explorer", "fanfic"],
      selected_goals: ["monetize"],
      experience_level: "advanced",
      skipped: false,
    });
    expect(getPreferredProjectType(mockUser?.id)).toBe("screenplay");
  });

  it("tolerates a partial server profile: syncs safe defaults and sends no unknown experience", async () => {
    mockGetState.mockResolvedValue({
      required: false,
      profile: { experience_level: "legendary", skipped: false, version: 1, completed_at: "2026-03-07T09:00:00.000Z" },
    });
    renderPage();

    await waitFor(() => expect(mockSavePersonaOnboardingData).toHaveBeenCalledWith("user-onboarding-1", {
      selected_personas: [],
      selected_goals: [],
      experience_level: "beginner",
      skipped: false,
    }));
    fireEvent.click(typeButton("novel"));

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    expect(mockSave.mock.calls[0][0]).toEqual({ selected_personas: ["serial"], selected_goals: [], skipped: false });
  });

  it("highlights the landing-page type for a new author, and the tap decides", async () => {
    setPreferredProjectType("novel", mockUser?.id);
    renderPage();

    expect(typeButton("novel")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("已带入你上次的选择")).not.toBeInTheDocument();
    fireEvent.click(typeButton("short"));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(getPreferredProjectType(mockUser?.id)).toBe("short");
  });

  it("saves skipped onboarding, keeps the type preference and redirects to /dashboard when source path is onboarding", async () => {
    mockLocationState = { from: { pathname: "/onboarding/persona" } };

    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "还没定？先随便看看" }));

    await waitFor(() => {
      expect(mockSavePersonaOnboardingData).toHaveBeenCalledWith("user-onboarding-1", {
        selected_personas: [],
        selected_goals: [],
        experience_level: "beginner",
        skipped: true,
      });
    });
    expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true });
    expect(getPreferredProjectType(mockUser?.id)).toBeNull();
    expect(mockReportActivationEvent).toHaveBeenCalledWith("user-onboarding-1", { event_name: "persona_submitted", skipped: true });
  });

  it("preserves source search/hash when navigating back", async () => {
    mockLocationState = {
      from: {
        pathname: "/project/project-1",
        search: "?file=file-2&tab=outline",
        hash: "#section-1",
      },
    };
    renderPage();

    fireEvent.click(typeButton("novel"));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith(
        "/project/project-1?file=file-2&tab=outline#section-1",
        { replace: true },
      );
    });
    expect(mockInitializeFirstWork).not.toHaveBeenCalled();
  });

  describe("on the default path", () => {
    beforeEach(() => {
      mockLocationState = { from: { pathname: "/dashboard", search: "", hash: "" } };
    });

    it("a chosen type opens the untouched first work", async () => {
      mockInitializeFirstWork.mockResolvedValue(firstWorkState());
      renderPage();

      fireEvent.click(typeButton("short"));

      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith("/project/first-project?file=first-file", { replace: true });
      });
      expect(mockNavigate).toHaveBeenCalledTimes(1);
      expect(mockInitializeFirstWork).toHaveBeenCalledWith("short", "zh");
      expect(queryClient.getQueryData(["workspace-first-work", "user-onboarding-1"])).toMatchObject({ project_id: "first-project" });
      // Desktop shows the AI panel next to the editor already.
      expect(mockRequestChatOnOpen).not.toHaveBeenCalled();
    });

    it("opens the first work on the AI tab on phones", async () => {
      mockIsMobile = true;
      mockInitializeFirstWork.mockResolvedValue(firstWorkState());
      renderPage();

      fireEvent.click(typeButton("novel"));

      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/project/first-project?file=first-file", { replace: true }));
      expect(mockRequestChatOnOpen).toHaveBeenCalledWith("first-project");
    });

    it.each([
      ["an account without a first work", () => mockInitializeFirstWork.mockResolvedValue(firstWorkState({ status: "ineligible", project_id: null, file_id: null, available: false, static_guidance: false }))],
      ["a first work the author already used", () => mockInitializeFirstWork.mockResolvedValue(firstWorkState({ first_ai_requested_at: "2026-11-09T00:00:00Z", static_guidance: false }))],
      ["a failed request", () => mockInitializeFirstWork.mockRejectedValue(new Error("network"))],
    ])("keeps the dashboard for %s", async (_label, arrange) => {
      arrange();
      renderPage();

      fireEvent.click(typeButton("novel"));

      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true }));
      expect(mockNavigate).toHaveBeenCalledTimes(1);
      expect(mockToastError).not.toHaveBeenCalled();
    });

    it("skipping looks around on the dashboard without creating the first work", async () => {
      renderPage();

      fireEvent.click(screen.getByRole("button", { name: "还没定？先随便看看" }));

      await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/dashboard", { replace: true }));
      expect(mockInitializeFirstWork).not.toHaveBeenCalled();
    });
  });

  it("stays retryable without caching or navigating when the API save fails", async () => {
    mockSave.mockRejectedValueOnce(new Error("network"));
    renderPage();

    fireEvent.click(typeButton("novel"));

    await waitFor(() => expect(mockToastError).toHaveBeenCalled());
    expect(mockSavePersonaOnboardingData).not.toHaveBeenCalled();
    expect(mockReportActivationEvent).not.toHaveBeenCalled();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(getPreferredProjectType(mockUser?.id)).toBeNull();
    await waitFor(() => expect(typeButton("novel")).toBeEnabled());

    fireEvent.click(typeButton("novel"));
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(2));
  });

  it("saves once when an option is double-tapped", async () => {
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

    fireEvent.click(typeButton("novel"));
    fireEvent.click(typeButton("novel"));
    fireEvent.click(typeButton("short"));

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1));
    resolveSave(undefined);
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledTimes(1);
  });

  it("falls back to a skipped profile when the server is older than the new identities", async () => {
    mockSave.mockRejectedValueOnce(new ApiError(400, "ERR_VALIDATION_ERROR"));
    renderPage();

    fireEvent.click(typeButton("screenplay"));

    await waitFor(() => expect(mockNavigate).toHaveBeenCalled());
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(mockSave.mock.calls[1][0]).toMatchObject({ selected_personas: [], skipped: true });
    expect(getPreferredProjectType(mockUser?.id)).toBe("screenplay");
  });

  it("does not retry validation errors when no new identity was sent, and retries at most once", async () => {
    mockSave.mockRejectedValueOnce(new ApiError(422, "ERR_VALIDATION_ERROR"));
    const { unmount } = renderPage();
    fireEvent.click(typeButton("novel"));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(1));
    expect(mockSave).toHaveBeenCalledTimes(1);
    unmount();

    mockSave.mockReset();
    mockSave.mockRejectedValue(new ApiError(422, "ERR_VALIDATION_ERROR"));
    renderPage();
    fireEvent.click(typeButton("short"));
    await waitFor(() => expect(mockToastError).toHaveBeenCalledTimes(2));
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(getPreferredProjectType(mockUser?.id)).toBeNull();
  });
});
