import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const LOCAL_IMPORT_RE = /@import\s+["'](\.[^"']+\.css)["']\s*;/g;

/**
 * Reads a CSS entry file and recursively expands relative `@import "./..."` rules
 * in cascade order so contract tests validate the full composed stylesheet.
 */
export function readComposedStylesheet(entryPath: string): string {
  const absoluteEntry = resolve(entryPath);
  const visited = new Set<string>();

  const expand = (filePath: string): string => {
    if (visited.has(filePath)) return "";
    visited.add(filePath);
    const dir = dirname(filePath);
    const content = readFileSync(filePath, "utf8");
    return content.replace(LOCAL_IMPORT_RE, (_match, relPath: string) =>
      expand(resolve(dir, relPath)),
    );
  };

  return expand(absoluteEntry);
}
