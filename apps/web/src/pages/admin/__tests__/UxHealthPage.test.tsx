import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { getUxHealthDashboard } = vi.hoisted(() => ({ getUxHealthDashboard: vi.fn() }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (
      key: string,
      fallbackOrOpts?: string | Record<string, unknown>,
      options?: Record<string, unknown>,
    ) => {
      const template =
        typeof fallbackOrOpts === "string"
          ? fallbackOrOpts
          : typeof fallbackOrOpts?.defaultValue === "string"
            ? fallbackOrOpts.defaultValue
            : key;
      const vars = typeof fallbackOrOpts === "string" ? options : fallbackOrOpts;
      return template.replace(/\{\{(\w+)\}\}/g, (_match, name: string) =>
        String(vars?.[name] ?? ""),
      );
    },
  }),
}));

vi.mock("../../../lib/adminApi", () => ({ adminApi: { getUxHealthDashboard } }));

import UxHealthPage from "../UxHealthPage";

const sampleDashboard = {
  days: 7 as const,
  timezone: "Asia/Shanghai",
  current: {
    period_start: "2026-09-30T16:00:00+00:00",
    period_end: "2026-10-07T08:00:00+00:00",
    metrics: {
      ttft_p50_ms: 1400,
      ttft_p95_ms: 8200,
      ttft_samples: 40,
      ttft_slow_tail_count: 2,
      ttft_slow_tail_rate: 0.05,
      duration_samples: 45,
      duration_slow_tail_count: 9,
      duration_slow_tail_rate: 0.2,
      cache_hit_tokens: 80000,
      cache_miss_tokens: 20000,
      prompt_cache_hit_rate: 0.8,
      total_rounds: 50,
      healthy_rounds: 44,
      healthy_round_rate: 0.88,
      user_stopped_rounds: 2,
      user_stopped_rate: 0.04,
      client_disconnected_rounds: 1,
      client_disconnected_rate: 0.02,
      abnormal_stop_rounds: 3,
      abnormal_stop_rate: 0.06,
      abnormal_stops: [
        { reason: "run_deadline_exceeded", count: 2, rate: 0.04 },
        { reason: "tool_failure_circuit_open", count: 1, rate: 0.02 },
      ],
      ai_edit_cards: 20,
      ai_edit_undone: 2,
      ai_edit_undo_rate: 0.1,
      ai_delete_cards: 4,
      ai_delete_restored: 1,
      ai_delete_restore_rate: 0.25,
      feedback_up: 16,
      feedback_down: 4,
      feedback_total: 20,
      thumbs_up_rate: 0.8,
      top_dislike_presets: [{ preset: "too_slow", count: 3 }],
      ttfv_p50_minutes: 8.5,
      ttfv_samples: 12,
      w1_returning_writers: 7,
      w1_eligible_writers: 10,
      w1_retention_rate: 0.7,
      chapters_written: 18,
      net_words_written: 24500,
      mobile_users: 6,
      desktop_users: 14,
      mobile_share: 0.3,
    },
  },
  previous: {
    period_start: "2026-09-23T16:00:00+00:00",
    period_end: "2026-09-30T08:00:00+00:00",
    metrics: {
      ttft_p50_ms: 1600,
      ttft_p95_ms: 9000,
      ttft_samples: 30,
      ttft_slow_tail_count: 3,
      ttft_slow_tail_rate: 0.1,
      duration_samples: 30,
      duration_slow_tail_count: 9,
      duration_slow_tail_rate: 0.3,
      cache_hit_tokens: 70000,
      cache_miss_tokens: 30000,
      prompt_cache_hit_rate: 0.7,
      total_rounds: 40,
      healthy_rounds: 34,
      healthy_round_rate: 0.85,
      user_stopped_rounds: 2,
      user_stopped_rate: 0.05,
      client_disconnected_rounds: 1,
      client_disconnected_rate: 0.025,
      abnormal_stop_rounds: 3,
      abnormal_stop_rate: 0.075,
      abnormal_stops: [],
      ai_edit_cards: 10,
      ai_edit_undone: 2,
      ai_edit_undo_rate: 0.2,
      ai_delete_cards: 2,
      ai_delete_restored: 0,
      ai_delete_restore_rate: 0,
      feedback_up: 14,
      feedback_down: 6,
      feedback_total: 20,
      thumbs_up_rate: 0.7,
      top_dislike_presets: [],
      ttfv_p50_minutes: 10.0,
      ttfv_samples: 10,
      w1_returning_writers: 5,
      w1_eligible_writers: 10,
      w1_retention_rate: 0.5,
      chapters_written: 12,
      net_words_written: 18000,
      mobile_users: 4,
      desktop_users: 16,
      mobile_share: 0.2,
    },
  },
  delta: {
    ttft_p50_ms: -200,
    ttft_p95_ms: -800,
    ttft_slow_tail_rate: -0.05,
    duration_slow_tail_rate: -0.1,
    prompt_cache_hit_rate: 0.1,
    healthy_round_rate: 0.03,
    user_stopped_rate: -0.01,
    client_disconnected_rate: -0.005,
    abnormal_stop_rate: -0.015,
    ai_edit_undo_rate: -0.1,
    ai_delete_restore_rate: 0.25,
    thumbs_up_rate: 0.1,
    ttfv_p50_minutes: -1.5,
    w1_retention_rate: 0.2,
    chapters_written: 6,
    net_words_written: 6500,
    mobile_share: 0.1,
  },
  daily: [
    {
      date: "2026-10-07",
      rounds: 15,
      healthy_rate: 0.9333,
      undo_rate: 0.05,
      ttft_p95_ms: 4200,
      thumbs_up_rate: 0.85,
    },
  ],
  definitions: {},
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <UxHealthPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );

describe("UxHealthPage", () => {
  beforeEach(() => {
    getUxHealthDashboard.mockReset();
    getUxHealthDashboard.mockResolvedValue(sampleDashboard);
  });

  it("renders KPI cards, abnormal stop breakdown, dislike presets, daily trend table, and methodology disclosure", async () => {
    renderPage();

    expect(await screen.findByText("1.4s / 8.2s")).toBeInTheDocument();
    expect(screen.getByText("88.0%")).toBeInTheDocument();
    expect(screen.getByText("18 章")).toBeInTheDocument();
    expect(screen.getByText("8.5 分钟")).toBeInTheDocument();
    expect(screen.getByText("run_deadline_exceeded")).toBeInTheDocument();
    expect(screen.getByText("too_slow")).toBeInTheDocument();

    const region = screen.getByRole("region", { name: "每日趋势" });
    expect(region).toHaveAttribute("tabindex", "0");
    const row = within(region).getByText("2026-10-07").closest("tr")!;
    expect(within(row).getByText("15")).toBeInTheDocument();
    expect(within(row).getByText("93.3%")).toBeInTheDocument();
    expect(within(row).getByText("4.2s")).toBeInTheDocument();

    expect(screen.getByText("统计口径")).toBeInTheDocument();
    expect(getUxHealthDashboard).toHaveBeenCalledWith(7);
  });

  it("switches time window via AdminSegmented", async () => {
    renderPage();
    await screen.findByText("1.4s / 8.2s");

    fireEvent.click(screen.getByRole("button", { name: "30天" }));
    await waitFor(() => expect(getUxHealthDashboard).toHaveBeenLastCalledWith(30));
    expect(screen.getByRole("button", { name: "30天" })).toHaveAttribute("aria-pressed", "true");
  });

  it("renders error state and retries", async () => {
    getUxHealthDashboard.mockRejectedValueOnce(new Error("ux health failed"));
    renderPage();

    expect(await screen.findByText("ux health failed")).toBeInTheDocument();
    getUxHealthDashboard.mockResolvedValueOnce(sampleDashboard);
    fireEvent.click(screen.getByRole("button", { name: "common:retry" }));
    expect(await screen.findByText("1.4s / 8.2s")).toBeInTheDocument();
  });
});
