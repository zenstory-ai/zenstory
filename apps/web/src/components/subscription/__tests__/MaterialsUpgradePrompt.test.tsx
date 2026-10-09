import { act, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MaterialsUpgradeNotice } from "../MaterialsUpgradePrompt";

const mockGetCatalog = vi.fn();

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, options?: Record<string, unknown>) => {
      let output = String(options?.defaultValue ?? _key);
      for (const [name, value] of Object.entries(options ?? {})) {
        if (name !== "defaultValue") output = output.replace(`{{${name}}}`, String(value));
      }
      return output;
    },
  }),
}));

vi.mock("../../../lib/subscriptionApi", () => ({
  subscriptionApi: { getCatalog: () => mockGetCatalog() },
}));

vi.mock("../UpgradePromptModal", () => ({
  UpgradePromptModal: () => null,
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("MaterialsUpgradeNotice", () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
});
