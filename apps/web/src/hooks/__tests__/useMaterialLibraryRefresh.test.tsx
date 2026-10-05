import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useRefreshMaterialLibraryOnCompletion } from "../useMaterialLibraryRefresh";

function setup(initial: Array<{ id: string; status: string }>) {
  const queryClient = new QueryClient();
  const invalidate = vi.spyOn(queryClient, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const view = renderHook(
    ({ materials }: { materials: Array<{ id: string; status: string }> }) =>
      useRefreshMaterialLibraryOnCompletion(materials),
    { wrapper, initialProps: { materials: initial } },
  );
  return { ...view, invalidate };
}

describe("useRefreshMaterialLibraryOnCompletion", () => {
  it("invalidates the sidebar summary when a decomposition finishes", () => {
    const { rerender, invalidate } = setup([{ id: "1", status: "processing" }]);
    expect(invalidate).not.toHaveBeenCalled();

    rerender({ materials: [{ id: "1", status: "completed" }] });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["material-library-summary"] });
  });

  it("also refreshes after a partially completed decomposition", () => {
    const { rerender, invalidate } = setup([{ id: "1", status: "pending" }]);

    rerender({ materials: [{ id: "1", status: "completed_with_errors" }] });

    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("does not refresh for materials that were already finished or that failed", () => {
    const { rerender, invalidate } = setup([
      { id: "1", status: "completed" },
      { id: "2", status: "processing" },
    ]);

    rerender({
      materials: [
        { id: "1", status: "completed" },
        { id: "2", status: "failed" },
      ],
    });

    expect(invalidate).not.toHaveBeenCalled();
  });
});
