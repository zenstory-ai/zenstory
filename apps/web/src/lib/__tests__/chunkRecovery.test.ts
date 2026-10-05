import { Component, Suspense, createElement, type ReactNode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

type ChunkRecovery = typeof import("../chunkRecovery");

class TestErrorBoundary extends Component<
  { children: ReactNode; onError: (error: Error) => void },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    this.props.onError(error);
  }

  render() {
    return this.state.failed ? createElement("div", null, "lazy crashed") : this.props.children;
  }
}

describe("chunkRecovery", () => {
  const reloadSpy = vi.fn();
  const originalLocation = window.location;
  let recovery: ChunkRecovery;

  beforeEach(async () => {
    vi.restoreAllMocks();
    vi.clearAllMocks();
    vi.resetModules();
    recovery = await import("../chunkRecovery");
    sessionStorage.clear();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        reload: reloadSpy,
      },
    });
  });

  it("detects stale dynamic import failures", () => {
    expect(recovery.isChunkLoadError(new Error("Failed to fetch dynamically imported module"))).toBe(true);
    expect(recovery.isChunkLoadError(new Error("ChunkLoadError: Loading chunk 1 failed"))).toBe(true);
    expect(recovery.isChunkLoadError(new Error("other error"))).toBe(false);
  });

  it("coordinates duplicate chunk failures behind one pending reload", async () => {
    const chunkError = new Error("Failed to fetch dynamically imported module");

    expect(recovery.reloadForChunkErrorOnce(chunkError, "first-signal")).toBe(true);
    expect(recovery.reloadForChunkErrorOnce(chunkError, "duplicate-signal")).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(1);

    const LazySuccess = recovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "already available") }),
      "already-available-route",
    );
    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "loading") },
        createElement(LazySuccess),
      ),
    );

    expect(await screen.findByText("already available")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("first-signal");
  });

  it("does not cancel Vite's preload rejection", () => {
    const registrations = vi
      .spyOn(window, "addEventListener")
      .mockImplementation(() => undefined);
    recovery.installChunkRecoveryHandlers();

    const listener = registrations.mock.calls.find(
      ([type]) => type === "vite:preloadError",
    )?.[1] as EventListener | undefined;
    expect(listener).toBeTypeOf("function");

    const event = new Event("vite:preloadError", { cancelable: true }) as Event & {
      payload?: unknown;
    };
    event.payload = new Error("Failed to fetch dynamically imported module");
    listener?.(event);

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(false);
  });

  it("clears the reload marker only after a successful import on a new page", async () => {
    sessionStorage.setItem("zenstory:chunk-reload-once", "lazy-route-success");
    const LazyComponent = recovery.lazyRoute(
      async () => ({
        default: () => createElement("div", null, "Lazy route content"),
      }),
      "lazy-route-success",
    );

    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "loading") },
        createElement(LazyComponent),
      ),
    );

    expect(await screen.findByText("Lazy route content")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBeNull();
  });

  it("does not clear an unknown legacy guard from an unrelated successful import", async () => {
    sessionStorage.setItem("zenstory:chunk-reload-once", "1");
    const LazyComponent = recovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "legacy page content") }),
      "some-other-route",
    );

    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "loading") },
        createElement(LazyComponent),
      ),
    );

    expect(await screen.findByText("legacy page content")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("1");
  });

  it("clears a generic guard after a later successful route import", async () => {
    // A hover preload failed and reloaded this tab a while ago.
    sessionStorage.setItem("zenstory:chunk-reload-once", "vite:preloadError");
    sessionStorage.setItem("zenstory:chunk-reload-at", String(Date.now() - 60_000));
    const LazyComponent = recovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "dashboard loaded") }),
      "Dashboard",
    );

    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "loading") },
        createElement(LazyComponent),
      ),
    );

    expect(await screen.findByText("dashboard loaded")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBeNull();
    expect(sessionStorage.getItem("zenstory:chunk-reload-at")).toBeNull();

    // So the next deploy in this tab can recover again.
    expect(
      recovery.reloadForChunkErrorOnce(
        new Error("Failed to fetch dynamically imported module: /assets/x.js"),
        "vite:preloadError",
      ),
    ).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("keeps a fresh generic guard so a failure on every load cannot loop", async () => {
    sessionStorage.setItem("zenstory:chunk-reload-once", "vite:preloadError");
    sessionStorage.setItem("zenstory:chunk-reload-at", String(Date.now() - 1_000));
    const LazyComponent = recovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "home loaded") }),
      "HomePage",
    );

    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "loading") },
        createElement(LazyComponent),
      ),
    );

    expect(await screen.findByText("home loaded")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("vite:preloadError");
  });

  it("propagates unrelated lazy import failures", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const unrelatedError = new Error("application module failed");
    const caughtError = vi.fn();
    const LazyComponent = recovery.lazyRoute(
      () => Promise.reject(unrelatedError),
      "unrelated-error-route",
    );

    render(
      createElement(
        TestErrorBoundary,
        { onError: caughtError },
        createElement(
          Suspense,
          { fallback: createElement("div", null, "loading") },
          createElement(LazyComponent),
        ),
      ),
    );

    await waitFor(() => expect(caughtError).toHaveBeenCalledWith(unrelatedError));
    expect(screen.getByText("lazy crashed")).toBeInTheDocument();
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("does not loop or clear the guard for a persistent failure on the new page", async () => {
    const chunkError = new Error("Failed to fetch dynamically imported module");
    expect(recovery.reloadForChunkErrorOnce(chunkError, "persistent-route")).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(1);

    vi.resetModules();
    const newPageRecovery: ChunkRecovery = await import("../chunkRecovery");
    expect(newPageRecovery.reloadForChunkErrorOnce(chunkError, "persistent-route")).toBe(false);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const caughtError = vi.fn();
    const LazyPersistentFailure = newPageRecovery.lazyRoute(
      () => Promise.reject(chunkError),
      "persistent-route",
    );
    render(
      createElement(
        TestErrorBoundary,
        { onError: caughtError },
        createElement(
          Suspense,
          { fallback: createElement("div", null, "loading") },
          createElement(LazyPersistentFailure),
        ),
      ),
    );

    await waitFor(() => expect(caughtError).toHaveBeenCalledWith(chunkError));

    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("persistent-route");
  });

  it("keeps React lazy suspended while a Vite preload failure is reloading", async () => {
    const registrations = vi
      .spyOn(window, "addEventListener")
      .mockImplementation(() => undefined);
    recovery.installChunkRecoveryHandlers();
    const preloadErrorListener = registrations.mock.calls.find(
      ([type]) => type === "vite:preloadError",
    )?.[1] as EventListener | undefined;
    const caughtError = vi.fn();
    const chunkError = new Error("Failed to fetch dynamically imported module");
    const vitePreload = async <T,>(load: () => Promise<T>): Promise<T> => {
      try {
        return await load();
      } catch (error) {
        const event = new Event("vite:preloadError", { cancelable: true }) as Event & {
          payload?: unknown;
        };
        event.payload = error;
        preloadErrorListener?.(event);
        if (!event.defaultPrevented) {
          throw error;
        }
        return undefined as T;
      }
    };
    const LazyComponent = recovery.lazyRoute(
      () => vitePreload(() => Promise.reject(chunkError)),
      "vite-lazy-route",
    );

    render(
      createElement(
        TestErrorBoundary,
        { onError: caughtError },
        createElement(
          Suspense,
          { fallback: createElement("div", null, "loading") },
          createElement(LazyComponent),
        ),
      ),
    );

    await waitFor(() => expect(reloadSpy).toHaveBeenCalledTimes(1));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("loading")).toBeInTheDocument();
    expect(caughtError).not.toHaveBeenCalled();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("vite-lazy-route");
  });

  it("keeps a nested child failure guard until that child imports successfully", async () => {
    const registrations = vi
      .spyOn(window, "addEventListener")
      .mockImplementation(() => undefined);
    recovery.installChunkRecoveryHandlers();
    const preloadErrorListener = registrations.mock.calls.find(
      ([type]) => type === "vite:preloadError",
    )?.[1] as EventListener | undefined;
    const chunkError = new Error("Failed to fetch dynamically imported module");
    const vitePreload = async <T,>(load: () => Promise<T>): Promise<T> => {
      try {
        return await load();
      } catch (error) {
        const event = new Event("vite:preloadError", { cancelable: true }) as Event & {
          payload?: unknown;
        };
        event.payload = error;
        preloadErrorListener?.(event);
        if (!event.defaultPrevented) {
          throw error;
        }
        return undefined as T;
      }
    };
    const OldPageChild = recovery.lazyRoute(
      () => vitePreload(() => Promise.reject(chunkError)),
      "DashboardHome",
    );
    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "old page loading") },
        createElement(OldPageChild),
      ),
    );

    await waitFor(() => expect(reloadSpy).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("DashboardHome");

    cleanup();
    vi.restoreAllMocks();
    vi.resetModules();
    const newPageRecovery: ChunkRecovery = await import("../chunkRecovery");
    const Parent = newPageRecovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "parent ready") }),
      "Dashboard",
    );
    const Sibling = newPageRecovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "sibling ready") }),
      "ProjectDashboardPage",
    );
    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "new page loading") },
        createElement(Parent),
        createElement(Sibling),
      ),
    );

    expect(await screen.findByText("parent ready")).toBeInTheDocument();
    expect(await screen.findByText("sibling ready")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("DashboardHome");

    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const caughtError = vi.fn();
    const PersistentChild = newPageRecovery.lazyRoute(
      () => Promise.reject(chunkError),
      "DashboardHome",
    );
    render(
      createElement(
        TestErrorBoundary,
        { onError: caughtError },
        createElement(
          Suspense,
          { fallback: createElement("div", null, "child loading") },
          createElement(PersistentChild),
        ),
      ),
    );

    await waitFor(() => expect(caughtError).toHaveBeenCalledWith(chunkError));
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBe("DashboardHome");

    const RecoveredChild = newPageRecovery.lazyRoute(
      async () => ({ default: () => createElement("div", null, "child recovered") }),
      "DashboardHome",
    );
    render(
      createElement(
        Suspense,
        { fallback: createElement("div", null, "child retry loading") },
        createElement(RecoveredChild),
      ),
    );

    expect(await screen.findByText("child recovered")).toBeInTheDocument();
    expect(sessionStorage.getItem("zenstory:chunk-reload-once")).toBeNull();
    expect(newPageRecovery.reloadForChunkErrorOnce(chunkError, "DashboardHome")).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(2);
  });
});
