/**
 * Runtime gaps of browsers the build still targets. Chromium before 93 (for
 * example Edge 92) has no Object.hasOwn, which react-markdown calls on every
 * render, so the chat panel crashed there.
 */
if (typeof Object.hasOwn !== "function") {
  Object.defineProperty(Object, "hasOwn", {
    configurable: true,
    writable: true,
    value: (object: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(object, key),
  });
}
