import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import InspirationManagement from "../InspirationManagement";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();

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

const sampleInspiration = {
  id: "insp-1",
  name: "Lighthouse mystery",
  description: "A keeper finds letters from the future",
  tags: ["mystery"],
  source: "community",
  status: "approved",
  is_featured: false,
  copy_count: 3,
  creator_id: "user-1",
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

const listWithOneInspiration = () =>
  useQueryMock.mockReturnValue({
    data: { items: [sampleInspiration], total: 1 },
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  });

describe("InspirationManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMutationMock.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    });
  });

  it("shows loading state", () => {
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<InspirationManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("load inspirations failed"),
      refetch: refetchMock,
    });

    render(<InspirationManagement />);
    expect(screen.getByText("load inspirations failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { items: [], total: 0 },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<InspirationManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("names the destructive action on the delete confirm button and deletes the chosen item", () => {
    const mutateMock = vi.fn();
    useMutationMock.mockReturnValue({ mutate: mutateMock, isPending: false });
    listWithOneInspiration();

    render(<InspirationManagement />);

    fireEvent.click(screen.getAllByTitle("common:delete")[0]);
    expect(screen.getByText("inspirations.deleteConfirm")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "inspirations.deleteTitle" })).toBeInTheDocument();
    // Mobile card + table row + the dialog naming the item about to be deleted.
    expect(screen.getAllByText("Lighthouse mystery")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: "common:confirm" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "inspirations.deleteTitle" }));
    expect(mutateMock).toHaveBeenCalledWith("insp-1");
  });

  it("locks the delete dialog and shows loading while the delete is in flight", () => {
    const mutateMock = vi.fn();
    useMutationMock.mockReturnValue({ mutate: mutateMock, isPending: true });
    listWithOneInspiration();

    render(<InspirationManagement />);

    fireEvent.click(screen.getAllByTitle("common:delete")[0]);
    expect(screen.getByRole("heading", { name: "inspirations.deleteTitle" })).toBeInTheDocument();

    const confirmButton = screen.getByRole("button", { name: "common:loading" });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "common:cancel" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "inspirations.deleteTitle" })).not.toBeInTheDocument();

    fireEvent.click(confirmButton);
    expect(mutateMock).not.toHaveBeenCalled();
  });

  it.each(["edit", "reject"])("locks every %s dialog close action while its mutation is pending", (kind) => {
    useMutationMock.mockReturnValue({ mutate: vi.fn(), isPending: true });
    useQueryMock.mockReturnValue({
      data: { items: [{ ...sampleInspiration, status: "pending" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });
    render(<InspirationManagement />);
    const action = kind === "edit" ? "common:edit" : "inspirations.reject";
    fireEvent.click(screen.getAllByTitle(action)[0]);
    const title = kind === "edit" ? "inspirations.editTitle" : "inspirations.rejectTitle";
    const heading = screen.getByRole("heading", { name: title });
    const close = heading.parentElement!.querySelector("button")!;
    expect(screen.getByRole("button", { name: "common:cancel" })).toBeDisabled();
    expect(close).toBeDisabled();
    fireEvent.click(close);
    expect(screen.getByRole("heading", { name: title })).toBeInTheDocument();
  });

  it.each(["edit", "reject"])("retains the ordinary %s header close action while idle", (kind) => {
    useMutationMock.mockReturnValue({ mutate: vi.fn(), isPending: false });
    useQueryMock.mockReturnValue({
      data: { items: [{ ...sampleInspiration, status: "pending" }], total: 1 },
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });
    render(<InspirationManagement />);
    const action = kind === "edit" ? "common:edit" : "inspirations.reject";
    fireEvent.click(screen.getAllByTitle(action)[0]);
    const title = kind === "edit" ? "inspirations.editTitle" : "inspirations.rejectTitle";
    const close = screen.getByRole("heading", { name: title }).parentElement!.querySelector("button")!;
    expect(close).toBeEnabled();
    fireEvent.click(close);
    expect(screen.queryByRole("heading", { name: title })).not.toBeInTheDocument();
  });
});
