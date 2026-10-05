import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import PromptManagement from "../PromptManagement";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const navigateMock = vi.fn();

const reloadMutateMock = vi.fn();
const deleteMutateMock = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

describe("PromptManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMutationMock.mockImplementation(
      ({ mutationFn }: { mutationFn?: (...args: unknown[]) => unknown }) => {
        if ((mutationFn?.length ?? 0) === 0) {
          return { mutate: reloadMutateMock, isPending: false };
        }
        return { mutate: deleteMutateMock, isPending: false };
      }
    );
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

    render(<PromptManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load prompts failed"),
      refetch: refetchMock,
    });

    render(<PromptManagement />);
    expect(screen.getByText("load prompts failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: [],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<PromptManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("handles create/reload/delete actions", () => {
    useQueryMock.mockReturnValue({
      data: [
        {
          id: "prompt-1",
          project_type: "novel",
          system_prompt: "You are a writer",
          is_active: true,
          version: 2,
          created_at: "2026-03-01T00:00:00Z",
          updated_at: "2026-03-08T00:00:00Z",
        },
      ],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<PromptManagement />);

    fireEvent.click(screen.getByRole("button", { name: "prompts.create" }));
    expect(navigateMock).toHaveBeenCalledWith("/admin/prompts/new");

    fireEvent.click(screen.getByRole("button", { name: "prompts.reload" }));
    expect(reloadMutateMock).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTitle("prompts.delete"));
    expect(screen.getByText("prompts.deleteConfirm")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "common:confirm" }));
    expect(deleteMutateMock).toHaveBeenCalledWith("novel");
  });

  it("adds consistent inner spacing to prompt cards", () => {
    useQueryMock.mockReturnValue({
      data: [
        {
          id: "prompt-1",
          project_type: "screenplay",
          system_prompt: "You are a screenwriter",
          is_active: true,
          version: 3,
          created_at: "2026-03-01T00:00:00Z",
          updated_at: "2026-03-08T00:00:00Z",
        },
      ],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<PromptManagement />);

    expect(screen.getByText("screenplay").closest(".admin-surface")).toHaveClass("p-4");
  });
});
