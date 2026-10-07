import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import UserManagement from "../../../pages/admin/UserManagement";
import AuditLogPage from "../../../pages/admin/AuditLogPage";
import { RecentActivityList } from "../RecentActivityList";

let timestamp = "";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" }, t: (key: string, options?: { count?: number }) =>
    options?.count === undefined ? key : `${key}:${options.count}` }),
}));
vi.mock("../../../lib/i18n-helpers", () => ({ getLocaleCode: () => "en-US" }));
vi.mock("../../../contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "admin" } }) }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: ({ queryKey }: { queryKey: string[] }) => ({
    data: queryKey[1] === "users" ? {
      users: [{ id: "writer", username: "writer", email: "writer@example.test", is_active: true,
        is_superuser: false, created_at: timestamp, updated_at: timestamp }], total: 1,
    } : {
      items: [{ id: "log", admin_id: "admin", admin_name: "admin", action: "update_user",
        resource_type: "user", created_at: timestamp }], total: 1, page: 1, page_size: 20,
    },
    isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
  }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

beforeEach(() => {
  // A test-owned non-UTC zone keeps the regression meaningful and portable in CI.
  vi.stubEnv("TZ", "Asia/Shanghai");
  expect(new Date("2026-04-08T09:00:00Z").getTimezoneOffset()).toBe(-480);
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-04-08T09:00:00Z"));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

it.each(["2026-04-08T08:55:00", "2026-04-08T08:55:00Z", "2026-04-08T16:55:00+08:00"])(
  "recent activity interprets %s as the same UTC instant", (value) => {
    timestamp = value;
    render(<RecentActivityList />);
    expect(screen.getByText("dashboard:time.minutesAgo:5")).toBeInTheDocument();
  },
);

it.each(["2026-04-08T08:55:00", "2026-04-08T08:55:00Z", "2026-04-08T16:55:00+08:00"])(
  "user list interprets %s as the same UTC instant", (value) => {
    timestamp = value;
    render(<UserManagement />, { wrapper: MemoryRouter });
    const expected = new Date("2026-04-08T08:55:00Z").toLocaleString("en-US", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
    // Both desktop table and mobile UserCard are mounted; neither may reinterpret UTC as local.
    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.getByText(`users.createdAt: ${expected}`)).toBeInTheDocument();
  },
);

it.each(["2026-04-08T08:55:00", "2026-04-08T08:55:00Z", "2026-04-08T16:55:00+08:00"])(
  "audit list interprets %s as the same UTC instant", (value) => {
    timestamp = value;
    render(<AuditLogPage />, { wrapper: MemoryRouter });
    const expected = new Date("2026-04-08T08:55:00Z").toLocaleString("en-US", {
      year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    expect(screen.getAllByText(expected).length).toBeGreaterThan(0);
  },
);
