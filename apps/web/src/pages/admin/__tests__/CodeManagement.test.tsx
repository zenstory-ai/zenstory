import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import CodeManagement from "../CodeManagement";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === "codes.batchCreateSuccess" && options?.count) {
        return `${key}:${options.count}`;
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

describe("CodeManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });

    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn(),
      },
    });
  });

  it.each([ ["create", true], ["create", false], ["batchCreate", true], ["batchCreate", false] ] as const)(
    "keeps code %s header close aligned with pending=%s", (kind, pending) => {
      useQueryMock.mockReturnValue({ data: { items: [], total: 0, page: 1, page_size: 20 }, isLoading: false, isFetching: false, isError: false, refetch: vi.fn() });
      useMutationMock.mockReturnValue({ mutate: mutateMock, isPending: pending });
      const { container } = render(<CodeManagement />);
      fireEvent.click(screen.getByRole("button", { name: `codes.${kind}` }));
      const modal = container.querySelector(".fixed.inset-0")!;
      const headerClose = modal.querySelector("button")!;
      expect(screen.getByRole("button", { name: "common:cancel" }).hasAttribute("disabled")).toBe(pending);
      expect(headerClose.hasAttribute("disabled")).toBe(pending);
      if (!pending) { fireEvent.click(headerClose); expect(container.querySelector(".fixed.inset-0")).toBeNull(); }
    },
  );

  it("shows loading state", () => {
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load codes failed"),
      refetch: refetchMock,
    });

    render(<CodeManagement />);
    expect(screen.getByText("load codes failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("confirms deactivation with the code and impact before mutating", () => {
    useQueryMock.mockReturnValue({
      data: {
        items: [
          {
            id: "code-1",
            code: "TESTCODE",
            tier: "pro",
            duration_days: 30,
            code_type: "single_use",
            max_uses: 1,
            current_uses: 0,
            is_active: true,
            notes: null,
            created_at: "2026-03-08T00:00:00Z",
            updated_at: "2026-03-08T00:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);

    mutateMock.mockClear();
    fireEvent.click(screen.getAllByTitle("codes.deactivate")[0]);

    expect(mutateMock).not.toHaveBeenCalled();
    expect(screen.getByText(/TESTCODE: codes.deactivateConfirmImpact/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "codes.confirmDeactivate" }));

    expect(mutateMock.mock.calls[0][0]).toEqual({
      id: "code-1",
      data: { is_active: false },
    });
  });

  it("disables the affected row while its update is pending", () => {
    let mutationCall = 0;
    useMutationMock.mockImplementation(() => {
      mutationCall += 1;
      return mutationCall === 3
        ? { mutate: mutateMock, isPending: true, variables: { id: "code-1" } }
        : { mutate: mutateMock, isPending: false };
    });
    useQueryMock.mockReturnValue({
      data: {
        items: [{
          id: "code-1",
          code: "INACTIVECODE",
          tier: "pro",
          duration_days: 30,
          code_type: "single_use",
          max_uses: 1,
          current_uses: 0,
          is_active: false,
          notes: null,
          created_at: "2026-03-08T00:00:00Z",
          updated_at: "2026-03-08T00:00:00Z",
        }],
        total: 1,
        page: 1,
        page_size: 20,
      },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);
    const activationButtons = screen.getAllByTitle("codes.activate");
    expect(activationButtons.every((button) => button.hasAttribute("disabled"))).toBe(true);
  });

  it("submits single and batch create", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<CodeManagement />);

    const createButtonsBefore = screen.getAllByRole("button", { name: "codes.create" });
    fireEvent.click(createButtonsBefore[0]);
    mutateMock.mockClear();
    const createButtonsAfter = screen.getAllByRole("button", { name: "codes.create" });
    fireEvent.click(createButtonsAfter[createButtonsAfter.length - 1]);

    expect(mutateMock).toHaveBeenCalledWith({
      tier: "pro",
      duration_days: 30,
      code_type: "single_use",
      max_uses: 1,
      notes: "",
    });

    const batchButtonsBefore = screen.getAllByRole("button", { name: "codes.batchCreate" });
    fireEvent.click(batchButtonsBefore[0]);
    mutateMock.mockClear();
    const batchButtonsAfter = screen.getAllByRole("button", { name: "codes.batchCreate" });
    fireEvent.click(batchButtonsAfter[batchButtonsAfter.length - 1]);

    expect(mutateMock).toHaveBeenCalledWith({
      tier: "pro",
      duration_days: 30,
      count: 10,
      code_type: "single_use",
      notes: "",
    });
  });
});
