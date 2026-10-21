import { useEffect } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => fallback ?? key,
  }),
}));

type AppUpdate = typeof import("../../lib/appUpdate");
type Prompt = typeof import("../AppUpdatePrompt");

let navigateTo: (path: string) => void = () => undefined;

function NavigationHandle() {
  const navigate = useNavigate();
  useEffect(() => {
    navigateTo = navigate;
  }, [navigate]);
  return null;
}

describe("AppUpdatePrompt", () => {
  const reloadSpy = vi.fn();
  const originalLocation = window.location;
  let appUpdate: AppUpdate;
  let AppUpdatePrompt: Prompt["AppUpdatePrompt"];

  beforeEach(async () => {
    vi.resetModules();
    appUpdate = await import("../../lib/appUpdate");
    ({ AppUpdatePrompt } = await import("../AppUpdatePrompt"));
    reloadSpy.mockReset();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, reload: reloadSpy },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  });

  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <NavigationHandle />
        <AppUpdatePrompt />
      </MemoryRouter>,
    );
  }

  it("offers a reload that the author can postpone", () => {
    renderAt("/project/p1");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    act(() => appUpdate.markAppUpdateRequired());
    expect(screen.getByText("需要刷新页面才能继续加载。如有正在编辑的内容，建议先保存。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "稍后" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("hides a suggested update when postponed", async () => {
    const doc = new DOMParser().parseFromString(
      '<html><head><script type="module" src="/assets/index-old.js"></script></head></html>',
      "text/html",
    );
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ entry: "/assets/index-new.js" }) })));
    renderAt("/dashboard");

    await act(() => appUpdate.checkForAppUpdate(doc));
    expect(screen.getByText("刷新页面即可使用新版本。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "稍后" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("loads the new build on the next route change outside the editor", () => {
    renderAt("/dashboard");
    act(() => appUpdate.markAppUpdateRequired());

    act(() => navigateTo("/dashboard/projects"));

    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("never reloads on its own when entering or inside the editor", () => {
    renderAt("/dashboard");
    act(() => appUpdate.markAppUpdateRequired());

    act(() => navigateTo("/project/p1"));
    act(() => navigateTo("/project/p2"));

    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("does not reload on route changes without an update", () => {
    renderAt("/dashboard");

    act(() => navigateTo("/dashboard/projects"));

    expect(reloadSpy).not.toHaveBeenCalled();
  });
});
