import { afterEach, describe, expect, it, vi } from "vitest";

describe("polyfills", () => {
  const nativeHasOwn = Object.hasOwn;

  afterEach(() => {
    Object.defineProperty(Object, "hasOwn", { configurable: true, writable: true, value: nativeHasOwn });
  });

  it("provides Object.hasOwn where the browser lacks it", async () => {
    Reflect.deleteProperty(Object, "hasOwn");
    vi.resetModules();

    await import("../polyfills");

    expect(Object.hasOwn({ title: "Ch 1" }, "title")).toBe(true);
    expect(Object.hasOwn(Object.create({ inherited: 1 }), "inherited")).toBe(false);
  });
});
