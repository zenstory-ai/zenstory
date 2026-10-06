import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import CheckInStatsPage from "../CheckInStatsPage";
import { MemoryRouter } from "react-router-dom";

const useQueryMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === "common:showing") {
        return `showing:${options?.from}-${options?.to}/${options?.total}`;
      }
      return key;
    },
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
}));

describe("CheckInStatsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockQueries = ({
    statsLoading = false,
    statsError = false,
    statsErrorMessage,
    statsData = {
      today_count: 30,
      yesterday_count: 25,
      week_total: 160,
      streak_distribution: { 1: 8, 3: 5, 7: 2 },
    },
    recordsLoading = false,
    recordsError = false,
    recordsErrorMessage,
    recordsData = {
      items: [
        {
          id: "rec-1",
          user_id: "user-1",
          username: "writer",
          check_in_date: "2026-03-08T00:00:00Z",
          streak_days: 7,
          points_earned: 20,
          created_at: "2026-03-08T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    },
  }: {
    statsLoading?: boolean;
    statsError?: boolean;
    statsErrorMessage?: string;
    statsData?: unknown;
    recordsLoading?: boolean;
    recordsError?: boolean;
    recordsErrorMessage?: string;
    recordsData?: unknown;
  }) => {
    const statsRefetchMock = vi.fn();
    const recordsRefetchMock = vi.fn();

    useQueryMock.mockImplementation(({ queryKey }: { queryKey?: unknown[] }) => {
      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "check-in" && queryKey[2] === "stats") {
        return {
          data: statsData,
          isLoading: statsLoading,
          isFetching: false,
          isError: statsError,
          error: statsErrorMessage ? new Error(statsErrorMessage) : null,
          refetch: statsRefetchMock,
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "check-in" && queryKey[2] === "records") {
        return {
          data: recordsData,
          isLoading: recordsLoading,
          isFetching: false,
          isError: recordsError,
          error: recordsErrorMessage ? new Error(recordsErrorMessage) : null,
          refetch: recordsRefetchMock,
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

    return { statsRefetchMock, recordsRefetchMock };
  };

  it("shows stats loading state", () => {
    mockQueries({ statsLoading: true });

    render(<CheckInStatsPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows stats error and supports retry", () => {
    const { statsRefetchMock } = mockQueries({
      statsError: true,
      statsErrorMessage: "load check-in stats failed",
    });

    render(<CheckInStatsPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("load check-in stats failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(statsRefetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders streak summary and records list", () => {
    mockQueries({});

    render(<CheckInStatsPage />, { wrapper: MemoryRouter });

    expect(screen.getByText("writer")).toBeInTheDocument();
    expect(screen.getByText("+20")).toBeInTheDocument();
    expect(screen.getAllByText(/checkIn.streakDistribution/).length).toBeGreaterThan(0);
  });

  it("shows records error and supports retry", () => {
    const { recordsRefetchMock } = mockQueries({
      recordsError: true,
      recordsErrorMessage: "load check-in records failed",
      recordsData: { items: [], total: 0, page: 1, page_size: 20 },
    });

    render(<CheckInStatsPage />, { wrapper: MemoryRouter });
    expect(screen.getByText("load check-in records failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(recordsRefetchMock).toHaveBeenCalledTimes(1);
  });
});
