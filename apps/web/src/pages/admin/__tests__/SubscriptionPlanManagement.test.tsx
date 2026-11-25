import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import SubscriptionPlanManagement from "../SubscriptionPlanManagement";

const useQueryMock = vi.fn();
const useMutationMock = vi.fn();
const invalidateQueriesMock = vi.fn();
const mutateMock = vi.fn();
const inspirationFeature = vi.hoisted(() => ({ enabled: true }));

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

vi.mock("../../../lib/toast", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("../../../config/inspirations", () => ({
  inspirationsConfig: inspirationFeature,
}));

const plan = {
  id: "plan-pro",
  name: "pro",
  display_name: "专业版",
  display_name_en: "Pro",
  price_monthly_cents: 1999,
  price_yearly_cents: 19999,
  features: {
    ai_conversations_per_day: 100,
    max_projects: 10,
    custom_prompts: true,
  },
  is_active: true,
};

describe("SubscriptionPlanManagement", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    inspirationFeature.enabled = true;
    useMutationMock.mockReturnValue({
      mutate: mutateMock,
      isPending: false,
    });
  });

  it.each([true, false])("keeps plan header close aligned with pending=%s", (pending) => {
    useQueryMock.mockReturnValue({ data: [plan], isLoading: false, isFetching: false, isError: false, refetch: vi.fn() });
    useMutationMock.mockReturnValue({ mutate: mutateMock, isPending: pending });
    const { container } = render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", { name: "plans.edit" }));
    const modal = container.ownerDocument.querySelector('[role="dialog"]')!;
    const headerClose = modal.querySelector("button")!;
    expect(screen.getByRole("button", { name: "common:cancel" }).hasAttribute("disabled")).toBe(pending);
    expect(headerClose.hasAttribute("disabled")).toBe(pending);
    if (!pending) { fireEvent.click(headerClose); expect(container.ownerDocument.querySelector('[role="dialog"]')).toBeNull(); }
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

    render(<SubscriptionPlanManagement />);
    expect(screen.getByText("common:loading")).toBeInTheDocument();
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

    render(<SubscriptionPlanManagement />);
    expect(screen.getByText("common:noData")).toBeInTheDocument();
  });

  it("validates features JSON before saving", () => {
    useQueryMock.mockReturnValue({
      data: [plan],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);

    fireEvent.click(screen.getByRole("button", { name: "plans.edit" }));

    fireEvent.change(screen.getByPlaceholderText("plans.featuresDetail.jsonPlaceholder"), {
      target: { value: "{" },
    });

    expect(screen.getByText("plans.invalidJson")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "plans.save" })).toBeDisabled();
    expect(mutateMock).not.toHaveBeenCalled();
  });

  it.each(["null", "[]", "42"])("rejects a JSON value that is not a feature object: %s", (json) => {
    useQueryMock.mockReturnValue({data: [plan]});
    render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", {name: "plans.edit"}));
    fireEvent.change(screen.getByLabelText("plans.featuresDetail.jsonLabel"), {target: {value: json}});
    fireEvent.click(screen.getByRole("button", {name: "plans.save"}));
    expect(screen.getByText("plans.invalidJson")).toBeInTheDocument();
    expect(mutateMock).not.toHaveBeenCalled();
  });

  it("submits updated plan payload", () => {
    useQueryMock.mockReturnValue({
      data: [plan],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);

    fireEvent.click(screen.getByRole("button", { name: "plans.edit" }));
    fireEvent.click(screen.getByRole("button", { name: "plans.save" }));

    expect(mutateMock).toHaveBeenCalledWith({
      planId: "plan-pro",
      data: {
        display_name: "专业版",
        display_name_en: "Pro",
        price_monthly_cents: 1999,
        price_yearly_cents: 19999,
        is_active: true,
        features: {
          ai_conversations_per_day: 100,
          max_projects: 10,
          custom_prompts: true,
          inspiration_copies_monthly: -1,
        },
      },
    });
  });

  it("shows the free daily policy as read-only and keeps it out of the JSON editor", () => {
    useQueryMock.mockReturnValue({data: [{...plan, name: "free", features: {ai_conversations_per_day: 10, max_projects: 3}, effective_features: {ai_conversations_per_day: 3}}]});
    render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", {name: "plans.edit"}));

    expect(screen.getByRole("spinbutton", {name: "plans.featuresDetail.ai_conversations_per_day"})).toHaveAttribute("readonly");
    expect(screen.getByText("plans.featuresDetail.freeDailyPolicy")).toBeInTheDocument();
    expect(JSON.parse((screen.getByLabelText("plans.featuresDetail.jsonLabel") as HTMLTextAreaElement).value)).toEqual({max_projects: 3});
    fireEvent.click(screen.getByRole("button", {name: "plans.save"}));
    expect(mutateMock).toHaveBeenCalledWith(expect.objectContaining({data: expect.objectContaining({features: {max_projects: 3, ai_conversations_per_day: 3}})}));
  });

  it("keeps Pro's existing unlimited inspiration policy read-only", () => {
    useQueryMock.mockReturnValue({data: [{...plan, features: {inspiration_copies_monthly: 100, max_projects: -1}}]});
    render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", {name: "plans.edit"}));
    expect(screen.getByRole("textbox", {name: "plans.featuresDetail.inspiration_copies_monthly"})).toHaveAttribute("readonly");
    expect(JSON.parse((screen.getByLabelText("plans.featuresDetail.jsonLabel") as HTMLTextAreaElement).value)).toEqual({max_projects: -1});
    fireEvent.click(screen.getByRole("button", {name: "plans.save"}));
    expect(mutateMock).toHaveBeenCalledWith(expect.objectContaining({data: expect.objectContaining({features: {max_projects: -1, inspiration_copies_monthly: -1}})}));
  });

  it("rejects a pasted free daily policy override before making a request", () => {
    useQueryMock.mockReturnValue({data: [{...plan, name: "free", features: {max_projects: 3}}]});
    render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", {name: "plans.edit"}));
    fireEvent.change(screen.getByLabelText("plans.featuresDetail.jsonLabel"), {target: {value: '{"ai_conversations_per_day": 10}'}});
    fireEvent.click(screen.getByRole("button", {name: "plans.save"}));
    expect(screen.getAllByText("plans.featuresDetail.freeDailyPolicy").length).toBeGreaterThan(0);
    expect(mutateMock).not.toHaveBeenCalled();
  });

  it("shows the effective entitlements users get, not the raw stored keys", () => {
    inspirationFeature.enabled = false;
    useQueryMock.mockReturnValue({
      data: [{
        ...plan,
        name: "free",
        features: {
          ai_conversations_per_day: 10, max_projects: 3, context_window_tokens: 4096, custom_prompts: false,
          material_uploads: 0, material_decompositions: 0, export_formats: ["txt", "md"],
          file_versions_per_file: 10, materials_library_access: false,
        },
        effective_features: {
          ai_conversations_per_day: 3, max_projects: 3, context_window_tokens: 4096, custom_prompts: false,
          material_uploads: 0, material_decompositions: 0, export_formats: ["txt"], file_versions_per_file: 10,
          materials_library_access: false, custom_skills: 3, inspiration_copies_monthly: 10, priority_support: false,
        },
      }],
      isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);

    const rows = screen.getAllByText(/^plans\.featuresDetail\./, { selector: "span" }).map((label) => [
      label.textContent, label.nextElementSibling?.textContent,
    ]);
    expect(rows).toEqual([
      ["plans.featuresDetail.ai_conversations_per_day", "3"],
      ["plans.featuresDetail.max_projects", "3"],
      ["plans.featuresDetail.custom_skills", "3"],
      ["plans.featuresDetail.file_versions_per_file", "10"],
      ["plans.featuresDetail.materials_library_access", "plans.no"],
      ["plans.featuresDetail.material_decompositions_monthly", "0"],
      ["plans.featuresDetail.export_formats", "txt"],
    ]);
    expect(screen.getByText("plans.featuresDetail.effectiveHint")).toBeInTheDocument();
    expect(screen.queryByText("materials_library_access")).not.toBeInTheDocument();
  });

  it("hides the inspiration entitlement from plan cards when disabled", () => {
    inspirationFeature.enabled = false;
    useQueryMock.mockReturnValue({
      data: [{
        ...plan,
        features: {
          ...plan.features,
          inspiration_copies_monthly: 10,
        },
      }],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);

    expect(screen.queryByText("plans.featuresDetail.inspiration_copies_monthly")).not.toBeInTheDocument();
  });

  it("does not present unenforced monthly writing or agent fields", () => {
    useQueryMock.mockReturnValue({
      data: [{
        ...plan,
        features: {
          ...plan.features,
          writing_credits_monthly: 600000,
          agent_runs_monthly: 120,
        },
      }],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);

    expect(screen.queryByText("plans.featuresDetail.writing_credits_monthly")).not.toBeInTheDocument();
    expect(screen.queryByText("plans.featuresDetail.agent_runs_monthly")).not.toBeInTheDocument();
  });

  it("warns and submits USD price changes when editing Pro USD prices", () => {
    useQueryMock.mockReturnValue({
      data: [{ ...plan, price_monthly_usd_cents: 999, price_yearly_usd_cents: 7900 }],
      isLoading: false,
      isFetching: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    render(<SubscriptionPlanManagement />);
    fireEvent.click(screen.getByRole("button", { name: "plans.edit" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("plans.monthlyPriceUsd"), { target: { value: "12.99" } });
    expect(screen.getByRole("alert")).toHaveTextContent("plans.usdPriceSyncWarning");

    fireEvent.click(screen.getByRole("button", { name: "plans.save" }));
    expect(mutateMock).toHaveBeenCalledWith(expect.objectContaining({
      planId: "plan-pro",
      data: expect.objectContaining({
        price_monthly_usd_cents: 1299,
        price_yearly_usd_cents: 7900,
      }),
    }));
  });
});
