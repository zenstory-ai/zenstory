import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaterialsUpgradeNotice, MaterialsUpgradePromptModal } from "../MaterialsUpgradePrompt";

const mockGetCatalog = vi.fn();
const mockGetQuota = vi.fn();
const mockGetPaymentOptions = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, options?: Record<string, unknown>) => {
      let output = String(options?.defaultValue ?? _key);
      for (const [name, value] of Object.entries(options ?? {})) {
        if (name !== "defaultValue") output = output.replace(`{{${name}}}`, String(value));
      }
      return output;
    },
    i18n: { language: "zh-CN" },
  }),
}));

vi.mock("../../../lib/subscriptionApi", () => ({
  subscriptionApi: {
    getCatalog: () => mockGetCatalog(),
    getQuota: () => mockGetQuota(),
  },
  subscriptionQueryKeys: {
    quota: () => ["subscription-quota", "test-user"],
  },
}));

vi.mock("../../../lib/paymentApi", () => ({
  paymentApi: { getOptions: () => mockGetPaymentOptions() },
  paymentQueryKeys: { options: () => ["payment-options"] },
}));

// Render what the shared modal receives, so the materials copy is visible.
vi.mock("../UpgradePromptModal", () => ({
  UpgradePromptModal: ({
    open,
    onClose,
    description,
    paidDescription,
    primaryLabel,
    onPrimary,
  }: {
    open: boolean;
    onClose: () => void;
    description: string;
    paidDescription?: string;
    primaryLabel: string;
    onPrimary: () => void;
  }) =>
    open ? (
      <div data-testid="upgrade-modal">
        <p data-testid="upgrade-description">{description}</p>
        {paidDescription && <p data-testid="upgrade-paid-description">{paidDescription}</p>}
        <button
          type="button"
          onClick={() => {
            onPrimary();
            onClose();
          }}
        >
          {primaryLabel}
        </button>
      </div>
    ) : null,
}));

vi.mock("../RedeemCodeModal", () => ({
  RedeemCodeModal: ({ isOpen, source }: { isOpen: boolean; source?: string }) =>
    isOpen ? <div data-testid="redeem-modal">{source}</div> : null,
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const freeQuota = {
  material_decompositions: { used: 0, limit: 0, reset_at: null },
};

describe("MaterialsUpgradeNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetPaymentOptions.mockResolvedValue({ enabled: true, payment_methods: [] });
    mockGetQuota.mockResolvedValue(freeQuota);
  });

  it("uses the Pro plan's monthly breakdown limit from the catalog", async () => {
    mockGetCatalog.mockResolvedValue({
      tiers: [{ name: "pro", entitlements: { material_decompositions_monthly: 12 } }],
    });

    render(<MaterialsUpgradeNotice source="test" />, { wrapper });

    expect(
      await screen.findByText("免费版不含素材库。开通 Pro 后，每月可拆解 12 次。"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开通 Pro" })).toBeInTheDocument();
  });

  it("drops the number when the catalog has no finite Pro limit", async () => {
    mockGetCatalog.mockResolvedValue({
      tiers: [{ name: "pro", entitlements: { material_decompositions_monthly: -1 } }],
    });

    render(<MaterialsUpgradeNotice source="test" />, { wrapper });
    await waitFor(() => expect(mockGetCatalog).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByText("免费版不含素材库，开通 Pro 后即可使用。")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("-1");
  });

  it("names the redeem code on its button while online checkout is off", async () => {
    mockGetCatalog.mockResolvedValue({ tiers: [] });
    mockGetPaymentOptions.mockResolvedValue({ enabled: false, payment_methods: [] });

    render(<MaterialsUpgradeNotice source="test" />, { wrapper });

    expect(await screen.findByRole("button", { name: "兑换码开通" })).toBeInTheDocument();
  });
});

describe("MaterialsUpgradePromptModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetCatalog.mockResolvedValue({
      tiers: [{ name: "pro", entitlements: { material_decompositions_monthly: 5 } }],
    });
    mockGetPaymentOptions.mockResolvedValue({ enabled: true, payment_methods: [] });
    mockGetQuota.mockResolvedValue(freeQuota);
  });

  it("offers the redeem code right here, and says why, while online checkout is off", async () => {
    mockGetPaymentOptions.mockResolvedValue({ enabled: false, payment_methods: [] });
    const onClose = vi.fn();

    render(<MaterialsUpgradePromptModal open onClose={onClose} source="materials_test" />, { wrapper });

    fireEvent.click(await screen.findByRole("button", { name: "兑换码开通" }));

    expect(screen.getByTestId("redeem-modal")).toHaveTextContent("materials_test");
    expect(onClose).toHaveBeenCalled();
  });

  it("explains in the paywall why the entry is a redeem code", async () => {
    mockGetPaymentOptions.mockResolvedValue({ enabled: false, payment_methods: [] });

    render(<MaterialsUpgradePromptModal open onClose={vi.fn()} />, { wrapper });

    await screen.findByRole("button", { name: "兑换码开通" });
    expect(screen.getByTestId("upgrade-description")).toHaveTextContent(
      "免费版不含素材库。开通 Pro 后，每月可拆解 5 次。暂时不能在线付款。有兑换码的话，点「兑换码开通」就能开通 Pro。",
    );
  });

  it("keeps 开通 Pro when the payment options cannot be read", async () => {
    mockGetPaymentOptions.mockRejectedValue(new Error("network"));

    render(<MaterialsUpgradePromptModal open onClose={vi.fn()} />, { wrapper });
    await waitFor(() => expect(mockGetPaymentOptions).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("button", { name: "开通 Pro" })).toBeInTheDocument();
    expect(screen.getByTestId("upgrade-description")).not.toHaveTextContent("暂时不能在线付款");
    expect(screen.queryByRole("button", { name: "兑换码开通" })).not.toBeInTheDocument();
  });

  it("tells a Pro author whose monthly breakdowns are used up when they come back", async () => {
    mockGetQuota.mockResolvedValue({
      material_decompositions: { used: 5, limit: 5, reset_at: "2026-10-31T16:00:00Z" },
    });

    render(<MaterialsUpgradePromptModal open onClose={vi.fn()} />, { wrapper });

    const paid = await screen.findByTestId("upgrade-paid-description");
    expect(paid.textContent).toMatch(
      /^本月 5 次拆解已用完，将于 (2026\/11\/01|11\/01\/2026) 恢复。已拆好的内容仍可查看和引用。$/,
    );
  });

  it("leaves the generic paid sentence when no monthly limit is reached", async () => {
    mockGetQuota.mockResolvedValue({
      material_decompositions: { used: 2, limit: 5, reset_at: "2026-10-31T16:00:00Z" },
    });

    render(<MaterialsUpgradePromptModal open onClose={vi.fn()} />, { wrapper });
    await waitFor(() => expect(mockGetQuota).toHaveBeenCalled());
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.queryByTestId("upgrade-paid-description")).not.toBeInTheDocument();
  });
});
