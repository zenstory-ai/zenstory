import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import PointsManagement from "../PointsManagement";
import { MemoryRouter } from "react-router-dom";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === "points.adjustSuccess" && options?.balance !== undefined) {
        return `${key}:${options.balance}`;
      }
      return key;
    },
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

vi.mock("../../../lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("PointsManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
  });

  const mockQueries = ({
    statsLoading = false,
    statsError = false,
    statsErrorMessage,
    statsData = {
      total_points_issued: 1000,
      total_points_spent: 300,
      total_points_expired: 50,
      active_users_with_points: 20,
    },
    userData,
    transactionsData,
  }: {
    statsLoading?: boolean;
    statsError?: boolean;
    statsErrorMessage?: string;
    statsData?: unknown;
    userData?: unknown;
    transactionsData?: unknown;
  }) => {
    const statsRefetchMock = vi.fn();

    useQueryMock.mockImplementation(({ queryKey }: { queryKey?: unknown[] }) => {
      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "points" && queryKey[2] === "stats") {
        return {
          data: statsData,
          isLoading: statsLoading,
          isFetching: false,
          isError: statsError,
          error: statsErrorMessage ? new Error(statsErrorMessage) : null,
          refetch: statsRefetchMock,
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "points" && queryKey[2] === "user" && queryKey[4] === undefined) {
        return {
          data: queryKey[3] ? userData : undefined,
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
          refetch: vi.fn(),
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "points" && queryKey[4] === "transactions") {
        return {
          data: queryKey[3] ? transactionsData : undefined,
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

  it("shows loading state", () => {
    mockQueries({ statsLoading: true });

    render(<PointsManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows stats error state and supports retry", () => {
    const { statsRefetchMock } = mockQueries({
      statsError: true,
      statsErrorMessage: "load points stats failed",
    });

    render(<PointsManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("load points stats failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(statsRefetchMock).toHaveBeenCalledTimes(1);
  });

  it("searches user and submits adjust points", () => {
    mockQueries({
      userData: {
        user_id: "user-1",
        username: "writer",
        email: "writer@example.com",
        available: 200,
        pending_expiration: 10,
        total_earned: 500,
        total_spent: 300,
      },
      transactionsData: {
        items: [
          {
            id: "tx-1",
            user_id: "user-1",
            username: "writer",
            amount: 50,
            balance_after: 200,
            transaction_type: "admin_adjust",
            source_id: null,
            description: "manual",
            expires_at: null,
            is_expired: false,
            created_at: "2026-03-08T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      },
    });

    render(<PointsManagement />, { wrapper: MemoryRouter });

    fireEvent.change(screen.getByPlaceholderText("points.searchUser"), {
      target: { value: "user-1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "common:search" }));

    expect(screen.getByText("writer")).toBeInTheDocument();
    expect(screen.getByText("manual")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "points.adjustPoints" }));

    fireEvent.change(screen.getByPlaceholderText("points.adjustAmountPlaceholder"), {
      target: { value: "50" },
    });
    fireEvent.change(screen.getByPlaceholderText("points.adjustReasonPlaceholder"), {
      target: { value: "manual bonus" },
    });
    fireEvent.click(screen.getByRole("button", { name: "common:confirm" }));

    expect(mutateMock).toHaveBeenCalledWith({
      userId: "user-1",
      data: {
        amount: 50,
        reason: "manual bonus",
      },
    });
  });
});
