export function safeAnnouncementUrl(value: string | null): string | null {
  if (!value || [...value].some((character) => {
    const code = character.charCodeAt(0);
    // Match the API's whitespace rejection, including Python's U+0085 whitespace.
    return character === "\\" || code < 32 || code === 127 || code === 0x85 || /\s/u.test(character);
  })) return null;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && url.port !== "0" ? url.href : null;
  } catch {
    return null;
  }
}
