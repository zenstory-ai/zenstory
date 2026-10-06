import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import UserManagement from "../UserManagement";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();

vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "admin-1", is_superuser: true } }) }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { from?: number; to?: number; total?: number }) =>
      key === "common:showing" && options
        ? `showing ${options.from}-${options.to} of ${options.total}`
        : key,
  }),
}));

vi.mock("@tanstack/react-query", () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
  useMutation: (...args: unknown[]) => useMutationMock(...args),
  useQueryClient: () => ({
    invalidateQueries: invalidateQueriesMock,
  }),
}));

describe("UserManagement", () => {
  const sampleUser = {
    id: "user-1",
    username: "writer",
    email: "writer@example.com",
    email_verified: true,
    is_active: true,
    is_superuser: false,
    created_at: "2026-03-01T00:00:00Z",
    updated_at: "2026-03-01T00:00:00Z",
  };

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
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
  });

  it("shows error state and supports retry", () => {
    const refetchMock = vi.fn();
    useQueryMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isFetching: false,
      isError: true,
      error: new Error("load users failed"),
      refetch: refetchMock,
    });

    render(<UserManagement />);
    expect(screen.getByText("load users failed")).toBeInTheDocument();

    fireEvent.click(screen.getByText("common:retry"));
    expect(refetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows empty state", () => {
    useQueryMock.mockReturnValue({
      data: { users: [], total: 0 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("submits search query", async () => {
    useQueryMock.mockReturnValue({
      data: { users: [sampleUser], total: 1 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);

    fireEvent.change(screen.getByPlaceholderText("users.search"), {
      target: { value: "writer" },
    });
    fireEvent.click(screen.getByRole("button", { name: "common:search" }));

    await waitFor(() => {
      expect(useQueryMock).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ["admin", "users", 0, "writer"],
        }),
      );
    });
  });

  it("opens edit modal and submits update payload", () => {
    const mutateMock = vi.fn();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
    useQueryMock.mockReturnValue({
      data: { users: [sampleUser], total: 1 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);

    fireEvent.click(screen.getAllByTitle("users.edit")[0]);
    expect(screen.getByText("users.editUser")).toBeInTheDocument();

    fireEvent.change(screen.getByDisplayValue("writer"), {
      target: { value: "writer-updated" },
    });
    mutateMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "common:save" }));

    expect(mutateMock).toHaveBeenCalledWith({
      id: "user-1",
      data: {
        username: "writer-updated",
        email: "writer@example.com",
        is_active: true,
        is_superuser: false,
      },
    });
  });

  it("does not offer self-deactivation or self-demotion in the edit form", () => {
    useQueryMock.mockReturnValue({ data: { users: [{ ...sampleUser, id: "admin-1", is_superuser: true }], total: 1 }, isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn() });
    render(<UserManagement />);
    fireEvent.click(screen.getAllByTitle("users.edit")[0]);
    expect(screen.getByRole("checkbox", { name: "users.isActive" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "users.isSuperuser" })).toBeDisabled();
  });

  it("opens delete modal and confirms delete", () => {
    const mutateMock = vi.fn();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
    useQueryMock.mockReturnValue({
      data: { users: [sampleUser], total: 1 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);

    fireEvent.click(screen.getAllByTitle("users.delete")[0]);
    expect(screen.getByText("users.deleteConfirm")).toBeInTheDocument();

    mutateMock.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "users.confirmDelete" }));
    expect(mutateMock).toHaveBeenCalledWith("user-1");
  });

  it("locks the delete dialog and shows loading while the delete is in flight", () => {
    const mutateMock = vi.fn();
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: true,
    });
    useQueryMock.mockReturnValue({
      data: { users: [sampleUser], total: 1 },
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<UserManagement />);

    fireEvent.click(screen.getAllByTitle("users.delete")[0]);
    expect(screen.getByText("users.deleteConfirm")).toBeInTheDocument();

    const confirmButton = screen.getByRole("button", { name: "common:loading" });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByRole("button", { name: "common:cancel" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "users.confirmDelete" })).not.toBeInTheDocument();

    fireEvent.click(confirmButton);
    expect(mutateMock).not.toHaveBeenCalled();
  });

  describe("pagination total", () => {
    const allUsers = Array.from({ length: 92 }, (_, index) => ({
      ...sampleUser,
      id: `user-${index + 1}`,
      username: index < 7 ? `reader-${index + 1}` : `writer-${index + 1}`,
      email: `user-${index + 1}@example.com`,
    }));

    // Serves pages the way the API does: `total` is the filtered count, not the rows so far.
    const serveUsers = (legacyArray = false) => {
      useQueryMock.mockImplementation(({ queryKey }: { queryKey: [string, string, number, string] }) => {
        const [, , page, search] = queryKey;
        const matching = search ? allUsers.filter((user) => user.username.includes(search)) : allUsers;
        const users = matching.slice(page * 20, page * 20 + 20);
        return {
          data: { users, total: legacyArray ? null : matching.length },
          isLoading: false,
          isFetching: false,
          isError: false,
          error: null,
          refetch: vi.fn(),
        };
      });
    };

    it("keeps the filtered total on the first, second and last page", () => {
      serveUsers();
      render(<UserManagement />);

      expect(screen.getByText("showing 1-20 of 92")).toBeInTheDocument();
      expect(screen.getByText("1 / 5")).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: "common:next" }));
      expect(screen.getByText("showing 21-40 of 92")).toBeInTheDocument();
      expect(screen.getByText("2 / 5")).toBeInTheDocument();

      for (let i = 0; i < 3; i += 1) {
        fireEvent.click(screen.getByRole("button", { name: "common:next" }));
      }
      expect(screen.getByText("showing 81-92 of 92")).toBeInTheDocument();
      expect(screen.getByText("5 / 5")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "common:next" })).toBeDisabled();
    });

    it("uses the search total and returns to the first page after searching", () => {
      serveUsers();
      render(<UserManagement />);
      fireEvent.click(screen.getByRole("button", { name: "common:next" }));
      expect(screen.getByText("2 / 5")).toBeInTheDocument();

      fireEvent.change(screen.getByPlaceholderText("users.search"), { target: { value: "writer" } });
      fireEvent.click(screen.getByRole("button", { name: "common:search" }));

      expect(useQueryMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ queryKey: ["admin", "users", 0, "writer"] }),
      );
      expect(screen.getByText("showing 1-20 of 85")).toBeInTheDocument();
      expect(screen.getByText("1 / 5")).toBeInTheDocument();

      fireEvent.change(screen.getByPlaceholderText("users.search"), { target: { value: "reader" } });
      fireEvent.click(screen.getByRole("button", { name: "common:search" }));
      // Seven matches fit on one page, so no pager is needed.
      expect(screen.queryByText(/^showing /)).not.toBeInTheDocument();
      expect(screen.getAllByText(/^reader-/).length).toBeGreaterThan(0);
    });

    it("falls back to the next-page estimate when an older API sends no total", () => {
      serveUsers(true);
      render(<UserManagement />);

      expect(screen.getByText("showing 1-20 of 20")).toBeInTheDocument();
      expect(screen.getByText("1 / 2")).toBeInTheDocument();
    });
  });
});
