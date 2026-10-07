import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import PointsManagement from "../PointsManagement";
import ReferralManagement from "../ReferralManagement";
import CheckInStatsPage from "../CheckInStatsPage";

let timestamp = "";
let mutationPending = false;
let total = 1;
let transactionAmount = 10;
let rewardType = "points";
let expires: string | null = "same";
const mutate = vi.fn();
let mutationOptions: { onSuccess?: (result: { new_balance: number; code: string }) => void; onError?: (error: Error) => void };
let queryKeys: unknown[][] = [];
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("../../../lib/i18n-helpers", () => ({ getLocaleCode: () => "en-US" }));
vi.mock("../../../lib/toast", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("@tanstack/react-query", () => ({
  useMutation: (options: typeof mutationOptions) => { mutationOptions = options; return { mutate, isPending: mutationPending }; },
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    queryKeys.push(queryKey);
    let data: unknown;
    if (queryKey[1] === "points") {
      if (queryKey[2] === "stats") data = { total_points_issued: 100, total_points_spent: 0,
        total_points_expired: 0, active_users_with_points: 1 };
      else if (queryKey[3] && queryKey[4] === "transactions") data = { items: [{ id: "tx", amount: transactionAmount,
        balance_after: 100, transaction_type: "check_in", created_at: timestamp }], total };
      else if (queryKey[3]) data = { user_id: "writer", username: "writer", email: "writer@example.test",
        available: 100, pending_expiration: 0, total_earned: 100, total_spent: 0 };
    } else if (queryKey[1] === "invites") data = { items: [{ id: "code", code: "CODE", owner_name: "writer",
      current_uses: 0, max_uses: 3, is_active: true, created_at: timestamp, expires_at: expires === "same" ? timestamp : expires }], total };
    else if (queryKey[1] === "referrals" && queryKey[2] === "rewards") data = { items: [{ id: "reward",
      username: "writer", reward_type: rewardType, amount: 10, is_used: false,
      created_at: timestamp, expires_at: expires === "same" ? timestamp : expires }], total };
    else if (queryKey[1] === "referrals") data = { total_codes: 1, active_codes: 1, total_referrals: 1,
      successful_referrals: 1, pending_rewards: 0, total_points_awarded: 10 };
    else if (queryKey[1] === "check-in" && queryKey[2] === "records") data = { items: [{ id: "check",
      user_id: "writer", username: "writer", check_in_date: "2026-04-08", streak_days: 1,
      points_earned: 10, created_at: timestamp }], total };
    else data = { today_count: 1, yesterday_count: 0, week_total: 1, streak_distribution: {} };
    return { data, isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn() };
  },
}));

beforeEach(() => {
  mutationPending = false; total = 1; transactionAmount = 10; rewardType = "points"; expires = "same"; queryKeys = [];
  vi.stubEnv("TZ", "Pacific/Honolulu");
  expect(new Date("2026-04-08T08:55:00Z").getTimezoneOffset()).toBe(600);
});
afterEach(() => { cleanup(); vi.unstubAllEnvs(); vi.clearAllMocks(); });

function renderPoints() {
  render(<PointsManagement />, { wrapper: MemoryRouter });
  fireEvent.change(screen.getByPlaceholderText("points.searchUser"), { target: { value: "writer" } });
  fireEvent.click(screen.getByText("common:search"));
}

const pageTimestampCases = ["points", "invites", "rewards", "check-in"].flatMap((page) =>
  ["2026-04-08T08:55:00", "2026-04-08T08:55:00Z", "2026-04-08T16:55:00+08:00"].map((value) => [page, value]),
);
it.each(pageTimestampCases)("%s timestamp %s preserves its UTC instant", (page, value) => {
  const expected = new Date("2026-04-08T08:55:00Z").toLocaleString("en-US", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  timestamp = value;
  if (page === "points") renderPoints();
  else if (page === "check-in") render(<CheckInStatsPage />, { wrapper: MemoryRouter });
  else { render(<ReferralManagement />, { wrapper: MemoryRouter }); if (page === "rewards") fireEvent.click(screen.getByText("referrals.rewards")); }
  expect(screen.getAllByText(expected).length).toBeGreaterThan(0);
});

it("check-in calendar day is not shifted to the previous local day", () => {
  timestamp = "2026-04-08T08:55:00Z";
  render(<CheckInStatsPage />, { wrapper: MemoryRouter });
  expect(screen.getByText("04/08/2026")).toBeInTheDocument();
});

it.each([true, false])("points adjust header retains pending=%s footer policy", (pending) => {
  timestamp = "2026-04-08T08:55:00Z"; mutationPending = pending;
  renderPoints(); fireEvent.click(screen.getByRole("button", { name: "points.adjustPoints" }));
  const heading = screen.getByRole("heading", { name: "points.adjustPoints" });
  const close = heading.parentElement!.querySelector("button")!;
  if (pending) expect(close).toBeDisabled(); else expect(close).not.toBeDisabled();
  fireEvent.click(close);
  if (pending) expect(heading).toBeInTheDocument(); else expect(heading).not.toBeInTheDocument();
});


it("points adjust inputs and callbacks retain validation and current outcomes", () => {
  timestamp = "invalid-date"; transactionAmount = -10;
  renderPoints();
  expect(screen.getAllByText("-").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "points.adjustPoints" }));
  fireEvent.change(screen.getByPlaceholderText("points.adjustAmountPlaceholder"), { target: { value: "12" } });
  fireEvent.change(screen.getByPlaceholderText("points.adjustReasonPlaceholder"), { target: { value: "Approved" } });
  fireEvent.click(screen.getByRole("button", { name: "common:confirm" }));
  expect(mutate).toHaveBeenCalledWith({ userId: "writer", data: { amount: 12, reason: "Approved" } });
  act(() => mutationOptions.onError?.(new Error("failed")));
  expect(screen.getByRole("heading", { name: "points.adjustPoints" })).toBeInTheDocument();
  act(() => mutationOptions.onSuccess?.({ new_balance: 112, code: "CODE" }));
  expect(screen.queryByRole("heading", { name: "points.adjustPoints" })).not.toBeInTheDocument();
});

it("points search Enter, paging and idle cancellation retain local state", () => {
  timestamp = "2026-04-08T08:55:00Z"; total = 45;
  render(<PointsManagement />, { wrapper: MemoryRouter });
  const input = screen.getByPlaceholderText("points.searchUser");
  fireEvent.change(input, { target: { value: "writer" } });
  fireEvent.keyDown(input, { key: "Enter" });
  fireEvent.click(screen.getByRole("button", { name: "common:next" }));
  expect(queryKeys.some((key) => key[1] === "points" && key[4] === "transactions" && key[5] === 2)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "common:previous" }));
  fireEvent.click(screen.getByRole("button", { name: "points.adjustPoints" }));
  fireEvent.click(screen.getByRole("button", { name: "common:cancel" }));
  expect(screen.queryByRole("heading", { name: "points.adjustPoints" })).not.toBeInTheDocument();
});

it("invite filter/paging/generation callbacks keep keyed state and error recovery", () => {
  timestamp = "invalid-date"; total = 45;
  render(<ReferralManagement />, { wrapper: MemoryRouter });
  expect(screen.getAllByText("-").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "common:next" }));
  expect(queryKeys.some((key) => key[1] === "invites" && key[2] === 2)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "common:previous" }));
  for (const [label, value] of [["referrals.activeOnly", true], ["referrals.inactiveOnly", false], ["referrals.allStatus", undefined]] as const) {
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(queryKeys.some((key) => key[1] === "invites" && key[2] === 1 && key[3] === value)).toBe(true);
  }
  fireEvent.click(screen.getByRole("button", { name: "referrals.generateButton" }));
  expect(mutate).toHaveBeenCalledTimes(1);
  act(() => mutationOptions.onError?.(new Error("provider failed")));
  act(() => mutationOptions.onError?.(new Error("")));
  act(() => mutationOptions.onSuccess?.({ code: "GENERATED", new_balance: 0 }));
  expect(screen.getByRole("button", { name: "referrals.generateButton" })).toBeEnabled();
});

it.each(["points", "pro_trial", "credits"])("reward type %s retains label, expiry and paging", (type) => {
  rewardType = type; timestamp = "2026-04-08T08:55:00Z"; expires = timestamp; total = 45;
  render(<ReferralManagement />, { wrapper: MemoryRouter });
  fireEvent.click(screen.getByText("referrals.rewards"));
  expect(screen.getByText("referrals.types." + type)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "common:next" }));
  expect(queryKeys.some((key) => key[1] === "referrals" && key[2] === "rewards" && key[3] === 2)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "common:previous" }));
  fireEvent.click(screen.getByRole("button", { name: "referrals.inviteCodes" }));
  expect(screen.getByRole("button", { name: "referrals.generateButton" })).toBeInTheDocument();
});

it("check-in pagination and invalid datetime fallback remain usable", () => {
  timestamp = "invalid-date"; total = 45;
  render(<CheckInStatsPage />, { wrapper: MemoryRouter });
  expect(screen.getByText("-")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "common:next" }));
  expect(queryKeys.some((key) => key[1] === "check-in" && key[2] === "records" && key[3] === 2)).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "common:previous" }));
  expect(screen.getByRole("button", { name: "common:previous" })).toBeDisabled();
});


it("nullable referral expiry retains the dash fallback", () => {
  timestamp = "2026-04-08T08:55:00Z"; expires = null;
  render(<ReferralManagement />, { wrapper: MemoryRouter });
  expect(screen.getByText("-")).toBeInTheDocument();
  fireEvent.click(screen.getByText("referrals.rewards"));
  expect(screen.getByText("-")).toBeInTheDocument();
});
