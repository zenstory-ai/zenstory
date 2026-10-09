/**
 * Chapter splitting for uploaded novels, ported from
 * apps/server/services/material/novel_text.py (split_novel_text /
 * truncate_novel_text). The server stays the source of truth (it splits and
 * cuts the uploaded file itself): the browser uses this only to pre-check the
 * characters a free trial covers and to tell the author how many chapters it
 * splits. Keep the heading rules in sync.
 *
 * Python's whitespace (str.strip, re \s) and "." differ from JavaScript's, so
 * the patterns below spell them out: PY_WS is str.isspace(), and "[^\n]"
 * stands for Python's "." (lines never contain "\n").
 */

// Chapters shorter than this are merged into a neighbour.
const MIN_CHAPTER_LENGTH = 100;
// A heading is a short line.
const MAX_HEADING_LENGTH = 40;
const SENTENCE_ENDINGS = ["。", "，", ",", "；", ";"];

// Python str.isspace(): no U+FEFF (JS trim strips it), plus \x1c-\x1f and U+0085.
const PY_WS_CHARS =
  "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const WS = `[${PY_WS_CHARS}]`;
const ANY = "[^\\n]";
const LEADING_WS_RE = new RegExp(`^${WS}+`, "u");
const TRAILING_WS_RE = new RegExp(`${WS}+$`, "u");

/** Python's str.strip() / str.rstrip(). */
export function pyStrip(text: string): string {
  return text.replace(LEADING_WS_RE, "").replace(TRAILING_WS_RE, "");
}
function pyRstrip(text: string): string {
  return text.replace(TRAILING_WS_RE, "");
}

const NUM = "[零〇一二两兩三四五六七八九十百千万萬0-9０-９]+";
const VOLUME_PREFIX = `(?:第${NUM}[卷部集篇]|卷${NUM})`;

const CHINESE_CHAPTER_RE = new RegExp(
  `^(?:${VOLUME_PREFIX}[^第]{0,20}?${WS}*)?【?第(${NUM})(?:[章节節]|回(?!合))】?${WS}*[:：、.．]?${WS}*(${ANY}*)$`,
  "u",
);
const VOLUME_ONLY_RE = new RegExp(
  `^${VOLUME_PREFIX}(?:${WS}*[:：·\\-—]?${WS}*[^第。，,]{0,30})?$`,
  "u",
);
const ENGLISH_NUMBER_WORDS = [
  "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
  "eighteen", "nineteen", "twenty",
];
// Python's \b and \d are Unicode-aware; spell that out for JavaScript.
const ENGLISH_CHAPTER_RE = new RegExp(
  `^chapter${WS}+(\\p{Nd}{1,4}|[ivxlcdm]+|${ENGLISH_NUMBER_WORDS.join("|")})(?![\\p{L}\\p{N}_])` +
    `${WS}*[:：.\\-—]?${WS}*(${ANY}*)$`,
  "iu",
);
const NUMBERED_RE = new RegExp(`^(\\p{Nd}{1,4})[.、．]${WS}*([^\\p{Nd}.．]${ANY}*)?$`, "u");
const SPECIAL_WORDS = [
  "楔子", "序章", "序言", "序幕", "引子", "引言", "前言", "序",
  "尾声", "尾聲", "后记", "後記", "终章", "終章", "番外",
];
const SPECIAL_RE = new RegExp(
  `^(${SPECIAL_WORDS.join("|")})(?:$|(?=[${PY_WS_CHARS}:：·\\-—篇零一二三四五六七八九十0-9]))${WS}*[:：·\\-—]?${WS}*(${ANY}*)$`,
  "u",
);

const SURROGATE_PAIR_RE = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;

/** Characters counted the way the server counts them (code points). */
export function countCharacters(text: string): number {
  const pairs = text.match(SURROGATE_PAIR_RE);
  return text.length - (pairs ? pairs.length : 0);
}

function isHeading(line: string): boolean {
  if (countCharacters(line) > MAX_HEADING_LENGTH) return false;
  if (SENTENCE_ENDINGS.some((ending) => line.endsWith(ending))) return false;
  return (
    CHINESE_CHAPTER_RE.test(line) ||
    ENGLISH_CHAPTER_RE.test(line) ||
    NUMBERED_RE.test(line) ||
    SPECIAL_RE.test(line)
  );
}

interface RawChapter {
  titleLine: string;
  lines: string[];
  startLine: number;
}

/** Start line (index into the normalized lines) of every chapter. */
function chapterStartLines(lines: string[]): number[] {
  const preamble: string[] = [];
  const rawChapters: RawChapter[] = [];
  let current: RawChapter | null = null;

  lines.forEach((rawLine, index) => {
    const line = pyStrip(rawLine);
    if (!line) {
      const target = current ? current.lines : preamble;
      if (target.length > 0) target.push("");
      return;
    }
    if (VOLUME_ONLY_RE.test(line) && countCharacters(line) <= MAX_HEADING_LENGTH) {
      return;
    }
    if (!isHeading(line)) {
      (current ? current.lines : preamble).push(line);
      return;
    }
    current = { titleLine: line, lines: [], startLine: index };
    rawChapters.push(current);
  });

  if (rawChapters.length === 0) return [];

  const preambleText = pyStrip(preamble.join("\n"));
  if (preambleText) {
    rawChapters.unshift({ titleLine: "", lines: [preambleText], startLine: 0 });
  }

  const starts: number[] = [];
  let carry = "";
  let carryStart: number | null = null;
  for (const raw of rawChapters) {
    let content = pyStrip(raw.lines.join("\n"));
    const startLine: number = carryStart ?? raw.startLine;
    if (carry) {
      content = pyStrip(`${carry}\n\n${content}`);
      carry = "";
      carryStart = null;
    }
    if (countCharacters(content) < MIN_CHAPTER_LENGTH) {
      // Merged into the previous chapter, or carried into the next one.
      if (starts.length === 0) {
        carry = [raw.titleLine, content].filter(Boolean).join("\n");
        carryStart = startLine;
      }
      continue;
    }
    starts.push(startLine);
  }
  return starts;
}

function normalizedLines(text: string): string[] {
  return text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
}

export interface NovelTruncation {
  text: string;
  totalChapters: number;
  keptChapters: number;
  truncated: boolean;
}

/**
 * Keep only the first `maxChapters` chapters, cut on the line where the next
 * chapter begins (same rule as the server, so the server splits the kept
 * text into exactly these chapters).
 */
export function truncateNovelText(text: string, maxChapters: number): NovelTruncation {
  const lines = normalizedLines(text);
  const starts = chapterStartLines(lines);
  const total = starts.length;
  if (maxChapters < 1 || total <= maxChapters) {
    return { text, totalChapters: total, keptChapters: total, truncated: false };
  }
  const keptText = pyRstrip(lines.slice(0, starts[maxChapters]).join("\n"));
  return { text: keptText, totalChapters: total, keptChapters: maxChapters, truncated: true };
}
