import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import QuotaManagement from "../QuotaManagement";

const useQueryMock = vi.fn();
const inspirationFeature = vi.hoisted(() => ({ enabled: true }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options && "used" in options ? `${key}:${options.used}` : key,
  }),
}));

const renderPage = () => render(<MemoryRouter><QuotaManagement /></MemoryRouter>);

const counter = (used: number, limit: number, reset_at: string | null = null) => ({ used, limit, reset_at });

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
}));

vi.mock("../../../config/inspirations", () => ({
  inspirationsConfig: inspirationFeature,
}));

describe("QuotaManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspirationFeature.enabled = true;
  });

  const mockQueries = ({
    statsLoading = false,
    statsError = false,
    statsErrorMessage,
    statsData = {
      period_start: "2026-10-01T00:00:00+00:00",
      period_end: "2026-11-01T00:00:00+00:00",
      material_decompositions: 80,
      skills_created: 30,
      inspiration_copies: 40,
    },
    userData,
  }: {
    statsLoading?: boolean;
    statsError?: boolean;
    statsErrorMessage?: string;
    statsData?: unknown;
    userData?: unknown;
  }) => {
    const statsRefetchMock = vi.fn();

    useQueryMock.mockImplementation(({ queryKey }: { queryKey?: unknown[] }) => {
      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "quota" && queryKey[2] === "stats") {
        return {
          data: statsData,
          isLoading: statsLoading,
          isFetching: false,
          isError: statsError,
          error: statsErrorMessage ? new Error(statsErrorMessage) : null,
          refetch: statsRefetchMock,
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "quota" && queryKey[2] === "user") {
        return {
          data: queryKey[3] ? userData : undefined,
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
          refetch: vi.fn(),
        };
      }

      return {
        data: undefined,
        isLoading: false,
        isFetching: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      };
    });

    return { statsRefetchMock };
  };

  it("shows stats loading state", () => {
    mockQueries({ statsLoading: true });

    renderPage();
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows stats error and supports retry", () => {
    const { statsRefetchMock } = mockQueries({
      statsError: true,
      statsErrorMessage: "load quota stats failed",
    });

    renderPage();
    expect(screen.getByText("load quota stats failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(statsRefetchMock).toHaveBeenCalledTimes(1);
  });

  it("searches user and renders quota usage detail", () => {
    mockQueries({
      userData: {
        user_id: "user-1",
        username: "writer",
        email: "writer@example.com",
        plan_name: "pro",
        plan_display_name: "专业版",
        plan_display_name_en: "Pro",
        ai_conversations: counter(50, -1, "2026-10-06T08:00:00+00:00"),
        material_decompositions: counter(2, 5, "2026-11-01T00:00:00+00:00"),
        custom_skills: counter(3, 20),
        inspiration_copies: counter(5, 10),
      },
    });

    renderPage();

    fireEvent.change(screen.getByPlaceholderText("quota.searchUser"), {
      target: { value: "user-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "common:search" }));

    expect(screen.getByText("writer")).toBeInTheDocument();
    expect(screen.getByText("专业版")).toBeInTheDocument();
    expect(screen.getByText("quota.usedUnlimited:50")).toBeInTheDocument();
    expect(screen.getByText("2 / 5")).toBeInTheDocument();
    expect(screen.getByText("3 / 20")).toBeInTheDocument();
    expect(screen.getAllByText("quota.materialDecompositions")).toHaveLength(2);
    expect(screen.queryByText("quota.materialUpload")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "users.viewDetails" })).toHaveAttribute("href", "/admin/users/user-1");
  });

  it("shows current-period totals without material uploads", () => {
    mockQueries({});

    renderPage();

    expect(screen.getByText("quota.period")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("quota.skillsCreated")).toBeInTheDocument();
    expect(screen.queryByText("quota.materialUploads")).not.toBeInTheDocument();
  });

  it("hides inspiration quota operations when disabled", () => {
    inspirationFeature.enabled = false;
    mockQueries({
      userData: {
        user_id: "user-1",
        username: "writer",
        plan_name: "pro",
        ai_conversations: counter(0, 20),
        material_decompositions: counter(0, 5),
        custom_skills: counter(0, 20),
        inspiration_copies: counter(5, 10),
      },
    });

    renderPage();
    expect(screen.queryByText("quota.inspirationCopies")).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("quota.searchUser"), {
      target: { value: "user-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "common:search" }));
    expect(screen.queryByText("quota.inspirationCopy")).not.toBeInTheDocument();
    expect(screen.queryByText("5 / 10")).not.toBeInTheDocument();
  });
});
