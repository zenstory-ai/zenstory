import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AppUpdate = typeof import("../appUpdate");

/** A built page, parsed rather than loaded, so the test DOM never fetches its script. */
function builtDocument(entry: string | null): Document {
  const script = entry ? `<script type="module" src="${entry}"></script>` : "";
  const doc = new DOMParser().parseFromString(`<html><head>${script}</head></html>`, "text/html");
  Object.defineProperty(doc, "visibilityState", { configurable: true, value: "visible" });
  return doc;
}

function respondWith(body: unknown, ok = true) {
  return vi.fn(async () => ({ ok, json: async () => body }) as Response);
}

describe("appUpdate", () => {
  let appUpdate: AppUpdate;
  let doc: Document;

  beforeEach(async () => {
    vi.resetModules();
    appUpdate = await import("../appUpdate");
    doc = builtDocument("/assets/index-old.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("offers an update when the deployed entry differs from this tab", async () => {
    vi.stubGlobal("fetch", respondWith({ entry: "/assets/index-new.js" }));
    const listener = vi.fn();
    appUpdate.subscribeAppUpdate(listener);

    await appUpdate.checkForAppUpdate(doc);

    expect(appUpdate.getAppUpdateState()).toEqual({ available: true, required: false });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("stays quiet for the same build, a failed request or a malformed manifest", async () => {
    for (const fetchMock of [
      respondWith({ entry: "/assets/index-old.js" }),
      respondWith({}, false),
      respondWith({ entry: 42 }),
      vi.fn(async () => {
        throw new TypeError("offline");
      }),
    ]) {
      vi.stubGlobal("fetch", fetchMock);
      await appUpdate.checkForAppUpdate(doc);
      expect(appUpdate.getAppUpdateState().available).toBe(false);
    }
  });

  it("does not check without a built entry script", async () => {
    doc = builtDocument(null);
    const fetchMock = respondWith({ entry: "/assets/index-new.js" });
    vi.stubGlobal("fetch", fetchMock);

    await appUpdate.checkForAppUpdate(doc);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps a dismissed build hidden until a newer one is deployed", async () => {
    vi.stubGlobal("fetch", respondWith({ entry: "/assets/index-new.js" }));
    await appUpdate.checkForAppUpdate(doc);
    appUpdate.dismissAppUpdate();
    await appUpdate.checkForAppUpdate(doc);
    expect(appUpdate.getAppUpdateState().available).toBe(false);

    vi.stubGlobal("fetch", respondWith({ entry: "/assets/index-newer.js" }));
    await appUpdate.checkForAppUpdate(doc);
    expect(appUpdate.getAppUpdateState().available).toBe(true);
  });

  it("cannot dismiss a required reload", () => {
    appUpdate.markAppUpdateRequired();
    appUpdate.dismissAppUpdate();

    expect(appUpdate.getAppUpdateState()).toEqual({ available: true, required: true });
  });

  it("checks when the tab becomes visible again, at most once a minute", async () => {
    vi.useFakeTimers();
    const fetchMock = respondWith({ entry: "/assets/index-old.js" });
    vi.stubGlobal("fetch", fetchMock);
    const stop = appUpdate.startAppUpdateChecks(doc);

    doc.dispatchEvent(new Event("visibilitychange"));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    doc.dispatchEvent(new Event("visibilitychange"));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(30 * 60_000);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    stop();
  });
});
