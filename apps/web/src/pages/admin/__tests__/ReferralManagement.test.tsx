import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import ReferralManagement from "../ReferralManagement";
import { MemoryRouter } from "react-router-dom";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

describe("ReferralManagement", () => {
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
      total_codes: 8,
      active_codes: 4,
      total_referrals: 12,
      successful_referrals: 6,
      pending_rewards: 2,
      total_points_awarded: 300,
    },
    codesData = {
      items: [
        {
          id: "code-1",
          code: "INVITE123",
          owner_name: "alice",
          current_uses: 1,
          max_uses: 5,
          is_active: true,
          expires_at: "2026-04-01T00:00:00Z",
          created_at: "2026-03-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    },
    rewardsData = {
      items: [
        {
          id: "reward-1",
          username: "bob",
          reward_type: "points",
          amount: 50,
          source: "invite",
          is_used: false,
          expires_at: "2026-04-01T00:00:00Z",
          created_at: "2026-03-01T00:00:00Z",
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
    codesData?: unknown;
    rewardsData?: unknown;
  }) => {
    const statsRefetchMock = vi.fn();

    useQueryMock.mockImplementation(({ queryKey }: { queryKey?: unknown[] }) => {
      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "referrals" && queryKey[2] === "stats") {
        return {
          data: statsData,
          isLoading: statsLoading,
          isFetching: false,
          isError: statsError,
          error: statsErrorMessage ? new Error(statsErrorMessage) : null,
          refetch: statsRefetchMock,
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "invites") {
        return {
          data: codesData,
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
          refetch: vi.fn(),
        };
      }

      if (Array.isArray(queryKey) && queryKey[0] === "admin" && queryKey[1] === "referrals" && queryKey[2] === "rewards") {
        return {
          data: rewardsData,
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

  it("shows loading state when stats are loading", () => {
    mockQueries({ statsLoading: true });

    render(<ReferralManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows stats error state and supports retry", () => {
    const { statsRefetchMock } = mockQueries({
      statsError: true,
      statsErrorMessage: "load referral stats failed",
    });

    render(<ReferralManagement />, { wrapper: MemoryRouter });
    expect(screen.getByText("load referral stats failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(statsRefetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders invite codes and rewards tabs", () => {
    mockQueries({});

    render(<ReferralManagement />, { wrapper: MemoryRouter });

    expect(screen.getByText("INVITE123")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "referrals.rewards" }));
    expect(screen.getByText("bob")).toBeInTheDocument();
  });

  it("triggers invite code generation", () => {
    mockQueries({});

    render(<ReferralManagement />, { wrapper: MemoryRouter });

    fireEvent.click(screen.getByRole("button", { name: "referrals.generateButton" }));
    expect(mutateMock).toHaveBeenCalledTimes(1);
  });
});
