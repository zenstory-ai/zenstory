import { describe, expect, it, vi } from "vitest";

vi.mock("../errorHandler", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../errorHandler")>()),
  translateError: (code: string) => `translated:${code}`,
}));

import { ApiError } from "../apiClient";
import {
  hasMaterialsLibraryAccess,
  isFeatureNotIncludedError,
  materialJobErrorText,
} from "../materialsAccess";

describe("materialsAccess", () => {
  it("reads the explicit entitlement before falling back to the tier", () => {
    expect(hasMaterialsLibraryAccess({ materials_library_access: false }, "pro")).toBe(false);
    expect(hasMaterialsLibraryAccess({}, "free")).toBe(false);
    expect(hasMaterialsLibraryAccess({}, "pro")).toBe(true);
    expect(hasMaterialsLibraryAccess(undefined, undefined)).toBeNull();
  });

  it("detects only the feature-not-included error", () => {
    expect(isFeatureNotIncludedError(new ApiError(402, "ERR_FEATURE_NOT_INCLUDED"))).toBe(true);
    expect(isFeatureNotIncludedError(new ApiError(402, "ERR_QUOTA_EXCEEDED"))).toBe(false);
    expect(isFeatureNotIncludedError(new Error("ERR_FEATURE_NOT_INCLUDED"))).toBe(false);
  });

  it("translates job failure codes and hides raw exception text", () => {
    expect(materialJobErrorText("ERR_MATERIAL_LLM_UNAVAILABLE")).toBe(
      "translated:ERR_MATERIAL_LLM_UNAVAILABLE",
    );
    expect(materialJobErrorText("httpx.ConnectError: http://prefect.railway.internal")).toBe(
      "translated:ERR_MATERIAL_DECOMPOSE_FAILED",
    );
    expect(materialJobErrorText(null)).toBeNull();
  });
});
