/**
 * The options an AI reply ends with, in the author's own words (the server's reply protocol asks
 * for at most three, written so the author can send them as they are), for example:
 *
 *   想换个方向的话，可以回我一句：
 *   - 「三皇子手里的京营改成四万」
 *   - 「开头再加一段，写他翻账册时发现有人动过手脚」
 *
 * Only a closing run of two or three wholly quoted items right after a line ending with a colon
 * counts. Anything else (a list of what was changed, text after the list) returns null, and the
 * chips are suggested by the model as before. See feature/2026-10-20-reply-options-as-chips.md.
 */
const LIST_MARKER = /^(?:[-*•]|\d+[.、)])\s*/;
const QUOTED = /^(?:「([^「」]+)」|“([^“”]+)”|"([^"]+)")[。.]?$/;
const MAX_OPTION_CHARS = 60;

export function optionsOfferedInReply(content: string): string[] | null {
  const lines = content
    .replace(/\[TASK_COMPLETE\]/g, "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  const options: string[] = [];
  let index = lines.length - 1;
  for (; index >= 0; index -= 1) {
    const match = QUOTED.exec(lines[index].replace(LIST_MARKER, ""));
    if (!match) break;
    const option = (match[1] ?? match[2] ?? match[3]).trim();
    if (!option || option.length > MAX_OPTION_CHARS) return null;
    options.unshift(option);
  }

  const intro = lines[index];
  if (!intro || !/[：:]$/.test(intro)) return null;
  if (options.length < 2 || options.length > 3) return null;
  return new Set(options).size === options.length ? options : null;
}
